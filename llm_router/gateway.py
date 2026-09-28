import time

from langchain.chat_models import init_chat_model


class GatewayError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(detail or code)


class StreamClosed(Exception):
    """The browser went away while a reply was streaming."""


def time_left_ms(started: float, max_latency_ms: int) -> float:
    return max_latency_ms - (time.monotonic() - started) * 1000


def is_rate_limited(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "429" in text or "rate limit" in text or "rate_limit" in text


def response_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content)


def complete(model_id: str, messages, timeout_s: float, max_tokens: int | None = None) -> dict:
    if timeout_s < 1:
        raise GatewayError("latency_budget_exceeded")
    llm = init_chat_model(
        model=model_id,
        model_provider="groq",
        timeout=timeout_s,
        max_tokens=max_tokens,
    )
    response = llm.invoke(messages)
    usage = getattr(response, "usage_metadata", None) or {}
    return {
        "text": response_text(response.content),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }


def complete_streaming(model_id: str, messages, timeout_s: float, max_tokens: int | None, on_delta) -> dict:
    if timeout_s < 1:
        raise GatewayError("latency_budget_exceeded")
    llm = init_chat_model(
        model=model_id,
        model_provider="groq",
        timeout=timeout_s,
        max_tokens=max_tokens,
    )
    parts: list[str] = []
    input_tokens = None
    output_tokens = None
    for chunk in llm.stream(messages):
        usage = getattr(chunk, "usage_metadata", None) or {}
        if usage.get("input_tokens"):
            input_tokens = usage["input_tokens"]
        if usage.get("output_tokens"):
            output_tokens = usage["output_tokens"]
        piece = response_text(getattr(chunk, "content", "") or "")
        if not piece:
            continue
        parts.append(piece)
        on_delta(piece)
    return {"text": "".join(parts), "input_tokens": input_tokens, "output_tokens": output_tokens}
