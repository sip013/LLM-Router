from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelRecord:
    model_id: str
    provider: str
    modalities: frozenset[str]
    context_window: int
    regions: frozenset[str]
    input_usd_per_million: float
    output_usd_per_million: float
    expected_latency_ms: int
    quality: dict[str, float]
    criteria: str
    # Used only to estimate cost for ranking. Not sent to the provider.
    output_token_cap: int = 1024
    max_output_tokens: int = 65536
    display_name: str = ""


@dataclass(frozen=True)
class Limits:
    max_latency_ms: int
    max_cost_usd: float
    residency_regions: frozenset[str]
    deny_model_ids: frozenset[str] = field(default_factory=frozenset)


class MalformedRequest(Exception):
    pass


# Relative priors, not a live price list. Eligibility uses them as ceilings.
# Quality is the starting estimate until a published quality table exists.
CATALOG: tuple[ModelRecord, ...] = (
    ModelRecord(
        model_id="openai/gpt-oss-20b",
        provider="groq",
        modalities=frozenset({"text"}),
        context_window=128_000,
        regions=frozenset({"us"}),
        input_usd_per_million=0.075,
        output_usd_per_million=0.30,
        expected_latency_ms=400,
        quality={"chat": 0.86, "code": 0.42, "extract": 0.70, "plan": 0.40},
        criteria="Greetings, short questions, definitions, basic arithmetic, and rewrites.",
        display_name="GPT-OSS 20B",
    ),
    ModelRecord(
        model_id="openai/gpt-oss-120b",
        provider="groq",
        modalities=frozenset({"text"}),
        context_window=128_000,
        regions=frozenset({"us"}),
        input_usd_per_million=0.15,
        output_usd_per_million=0.75,
        expected_latency_ms=900,
        quality={"chat": 0.80, "code": 0.90, "extract": 0.84, "plan": 0.88},
        criteria="Coding, debugging, explaining an algorithm and writing it, proofs, and multi-step planning.",
        display_name="GPT-OSS 120B",
    ),
    ModelRecord(
        model_id="qwen/qwen3.8-27b",
        provider="groq",
        modalities=frozenset({"text", "image"}),
        context_window=128_000,
        regions=frozenset({"us"}),
        input_usd_per_million=0.20,
        output_usd_per_million=0.80,
        expected_latency_ms=1000,
        quality={"chat": 0.55, "code": 0.60, "extract": 0.58, "plan": 0.62, "image": 0.90},
        criteria="Image understanding, OCR, charts, and questions about an attached picture.",
        display_name="Qwen 3.8 27B",
    ),
)

BY_ID = {model.model_id: model for model in CATALOG}

DEFAULT_LIMITS = Limits(
    max_latency_ms=180_000,
    max_cost_usd=1.0,
    residency_regions=frozenset({"us"}),
)


def narrow_limits(tenant: Limits, caller: Limits | None) -> Limits:
    if caller is None:
        return tenant
    if caller.max_latency_ms > tenant.max_latency_ms:
        raise MalformedRequest("A caller cannot raise the latency ceiling.")
    if caller.max_cost_usd > tenant.max_cost_usd:
        raise MalformedRequest("A caller cannot raise the cost ceiling.")
    if not caller.residency_regions.issubset(tenant.residency_regions):
        raise MalformedRequest("A caller cannot add a residency region.")
    return Limits(
        max_latency_ms=caller.max_latency_ms,
        max_cost_usd=caller.max_cost_usd,
        residency_regions=caller.residency_regions,
        deny_model_ids=tenant.deny_model_ids | caller.deny_model_ids,
    )
