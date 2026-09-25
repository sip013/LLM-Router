from llm_router.eligibility import eligible_models
from llm_router.jev_client import JevUnavailable, ask
from llm_router.policy import SHORT_TURN_CHARS, rank_models
from llm_router.registry import DEFAULT_LIMITS, Limits


class NoEligibleModel(Exception):
    def __init__(self, rejected: list[dict]):
        self.rejected = rejected
        super().__init__("No eligible model")


def route_request(
    user_text: str,
    context_tokens: int,
    has_image: bool,
    previous_model_id: str | None = None,
    limits: Limits = DEFAULT_LIMITS,
    jev_timeout: float = 5.0,
) -> dict:
    eligible, rejected = eligible_models(limits, context_tokens, has_image)
    if not eligible:
        raise NoEligibleModel(rejected)

    analysis_source = "single_eligible"
    jev = None
    task_type = "image" if has_image else "chat"
    preferred = None

    if len(eligible) == 1:
        preferred = eligible[0].model_id
    elif has_image:
        analysis_source = "modality"
        task_type = "image"
    else:
        try:
            jev = ask(
                {"latest_user_message": user_text, "has_image": False},
                {model.model_id: model.criteria for model in eligible},
                timeout=jev_timeout,
            )
            analysis_source = "jev"
            if jev["task_type"]:
                task_type = jev["task_type"]
            if jev["route"]:
                preferred = jev["route"]
            elif (
                previous_model_id
                and any(model.model_id == previous_model_id for model in eligible)
                and len(user_text) <= SHORT_TURN_CHARS
            ):
                preferred = previous_model_id
                analysis_source = "jev_low_confidence_stickiness"
            else:
                analysis_source = "jev_low_confidence_policy"
        except JevUnavailable as exc:
            jev = {"error": str(exc)}
            analysis_source = "fallback_heuristic"
            if (
                previous_model_id
                and any(model.model_id == previous_model_id for model in eligible)
                and len(user_text) <= SHORT_TURN_CHARS
            ):
                preferred = previous_model_id

    ranked = rank_models(eligible, task_type, context_tokens, preferred)
    return {
        "ranked": [{"model_id": model.model_id, "score": round(score, 4)} for model, score in ranked],
        "selected": ranked[0][0].model_id,
        "fallbacks": [model.model_id for model, _score in ranked[1:2]],
        "rejected": rejected,
        "analysis_source": analysis_source,
        "task_type": task_type,
        "needs_verification": bool(jev and jev.get("needs_verification")),
        "jev": None if jev is None else {key: value for key, value in jev.items() if key != "usage"},
        "limits": {
            "max_latency_ms": limits.max_latency_ms,
            "max_cost_usd": limits.max_cost_usd,
            "residency_regions": sorted(limits.residency_regions),
        },
    }
