import hashlib
import time
import uuid

from llm_router.gateway import GatewayError, StreamClosed, complete, complete_streaming, is_rate_limited, time_left_ms
from llm_router.pipeline import route_request
from llm_router.policy import rank_models
from llm_router.quality import load_policy, load_rates
from llm_router.registry import BY_ID, DEFAULT_LIMITS
from llm_router.trace import append_trace
from llm_router.validate import matches_schema, verifier_model, verify_answer


def canary_turn(request_id: str, fraction: float) -> bool:
    if fraction <= 0:
        return False
    bucket = int(hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return bucket < int(fraction * 100)


def _ranked_rows(models, task_type, context_tokens, preferred_id, rates) -> list[dict]:
    ranked = rank_models(models, task_type, context_tokens, preferred_id, rates)
    return [{"model_id": model.model_id, "score": round(score, 4)} for model, score in ranked]


def run_turn(
    history: list[dict],
    image_path: str,
    previous_model_id: str | None,
    messages_for,
    require_verification: bool = False,
    response_schema: dict | None = None,
    limits=DEFAULT_LIMITS,
    trace_path=None,
    on_start=None,
    on_delta=None,
    on_reset=None,
) -> dict:
    user_text = history[-1]["text"]
    context_tokens = max(1, sum(len(turn["text"]) for turn in history) // 4)
    decision = route_request(
        user_text=user_text,
        context_tokens=context_tokens,
        has_image=bool(image_path),
        previous_model_id=previous_model_id,
        limits=limits,
    )
    policy = load_policy()
    request_id = str(uuid.uuid4())
    live_rates = load_rates(policy.get("quality_table"))
    candidate_rates = load_rates(policy.get("candidate_quality_table"))
    models = [BY_ID[row["model_id"]] for row in decision["ranked"]]
    jev = decision.get("jev") or {}
    preferred = decision["selected"] if jev.get("route") or "stickiness" in decision["analysis_source"] else None
    live_ranked = _ranked_rows(models, decision["task_type"], context_tokens, preferred, live_rates or None)
    candidate_ranked = None
    served = live_ranked
    experiment = None
    if candidate_rates and policy.get("shadow"):
        candidate_ranked = _ranked_rows(models, decision["task_type"], context_tokens, preferred, candidate_rates)
    if candidate_rates and canary_turn(request_id, float(policy.get("canary_fraction") or 0)):
        candidate_ranked = candidate_ranked or _ranked_rows(
            models, decision["task_type"], context_tokens, preferred, candidate_rates
        )
        served = candidate_ranked
        experiment = "canary"
    decision["ranked"] = served
    decision["selected"] = served[0]["model_id"]
    decision["fallbacks"] = [row["model_id"] for row in served[1:2]]

    record = {
        "request_id": request_id,
        "status": "started",
        "user_text": user_text,
        "decision": decision,
        "shadow_ranked": candidate_ranked,
        "experiment": experiment,
        "include_in_quality": experiment != "canary",
        "task_type": decision["task_type"],
        "attempts": [],
    }
    if trace_path is not None:
        append_trace(record, trace_path)

    started = time.monotonic()
    blocked_providers = set()
    attempts = []
    text = None
    model_id = None
    validation = "not_required"
    verification = "not_run"
    attempt_ids = [decision["selected"], *decision["fallbacks"]]

    for index, candidate_id in enumerate(attempt_ids):
        model = BY_ID[candidate_id]
        trigger = "initial" if index == 0 else "next_ranked"
        if model.provider in blocked_providers:
            attempts.append({"model_id": candidate_id, "trigger": "skipped_provider", "error": "rate_limited"})
            continue
        remaining_ms = time_left_ms(started, limits.max_latency_ms)
        if remaining_ms < 1000:
            attempts.append({"model_id": candidate_id, "trigger": trigger, "error": "latency_budget_exceeded"})
            break
        shown = False

        def publish(piece: str) -> None:
            nonlocal shown
            shown = True
            on_delta(piece)

        def abandon() -> None:
            nonlocal shown
            if shown and on_reset is not None:
                on_reset()
            shown = False

        for retry in range(2):
            try:
                if on_start is not None:
                    on_start(candidate_id, decision)
                if on_delta is None:
                    result = complete(
                        candidate_id,
                        messages_for(history, candidate_id, image_path),
                        timeout_s=remaining_ms / 1000,
                        max_tokens=model.max_output_tokens,
                    )
                else:
                    result = complete_streaming(
                        candidate_id,
                        messages_for(history, candidate_id, image_path),
                        remaining_ms / 1000,
                        model.max_output_tokens,
                        publish,
                    )
                text = result["text"]
                model_id = candidate_id
                ok, validation = matches_schema(text, response_schema)
                attempts.append({
                    "model_id": candidate_id,
                    "trigger": trigger if retry == 0 else "transport_retry",
                    "error": None if ok else validation,
                    "input_tokens": result.get("input_tokens"),
                    "output_tokens": result.get("output_tokens"),
                })
                break
            except StreamClosed:
                raise
            except GatewayError as exc:
                abandon()
                attempts.append({"model_id": candidate_id, "trigger": trigger, "error": exc.code})
                text = None
                break
            except Exception as exc:
                abandon()
                if is_rate_limited(exc):
                    blocked_providers.add(model.provider)
                    attempts.append({"model_id": candidate_id, "trigger": trigger, "error": "rate_limited"})
                    text = None
                    break
                attempts.append({
                    "model_id": candidate_id,
                    "trigger": trigger if retry == 0 else "transport_retry",
                    "error": type(exc).__name__,
                })
                if retry == 0:
                    continue
                text = None
        if text is not None:
            break

    if text is None:
        record["status"] = "failed"
        record["attempts"] = attempts
        if trace_path is not None:
            append_trace(record, trace_path)
        raise RuntimeError(attempts[-1]["error"] if attempts else "all_models_failed")

    should_verify = (require_verification or decision.get("needs_verification")) and validation in {"ok", "not_required"}
    if should_verify:
        checker = verifier_model(model_id, [row["model_id"] for row in served])
        remaining_ms = time_left_ms(started, limits.max_latency_ms)
        if checker is None or remaining_ms < 1000:
            verification = "verification_unavailable"
        else:
            try:
                accepted, verification = verify_answer(user_text, text, checker, remaining_ms / 1000)
                if not accepted and verification == "reject":
                    verification = "verification_rejected"
            except Exception:
                verification = "verification_unavailable"

    record["status"] = "completed"
    record["attempts"] = attempts
    record["returned_model_id"] = model_id
    record["validation"] = validation
    record["verification"] = verification
    if trace_path is not None:
        append_trace(record, trace_path)
    return {
        "text": text,
        "model_id": model_id,
        "decision": decision,
        "validation": validation,
        "verification": verification,
        "request_id": request_id,
        "attempts": attempts,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }
