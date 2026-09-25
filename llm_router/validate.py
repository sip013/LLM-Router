import json

from langchain_core.messages import HumanMessage
from llm_router.gateway import complete


def matches_schema(text: str, schema: dict | None) -> tuple[bool, str]:
    if not schema:
        return True, "not_required"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False, "invalid_json"
    if schema.get("type") == "object" and not isinstance(payload, dict):
        return False, "not_an_object"
    missing = [key for key in schema.get("required", []) if isinstance(payload, dict) and key not in payload]
    if missing:
        return False, "missing_required"
    return True, "ok"


def verifier_model(selected_id: str, ranked_ids: list[str]) -> str | None:
    for model_id in ranked_ids:
        if model_id != selected_id:
            return model_id
    return None


def verify_answer(question: str, answer: str, verifier_id: str, timeout_s: float) -> tuple[bool, str]:
    message = HumanMessage(
        content=(
            "Reply with one word, accept or reject. "
            f"Question: {question}\nAnswer: {answer}"
        )
    )
    result = complete(verifier_id, [message], timeout_s=timeout_s)
    word = result["text"].strip().split()
    verdict = word[0].lower().strip(".,") if word else ""
    if verdict not in {"accept", "reject"}:
        return False, "verification_unavailable"
    return verdict == "accept", verdict
