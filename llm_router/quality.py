import json
from pathlib import Path

POLICY_PATH = Path("data/policy.json")
DEFAULT_POLICY = {
    "quality_table": None,
    "candidate_quality_table": None,
    "shadow": False,
    "canary_fraction": 0,
}


def load_policy(path: Path = POLICY_PATH) -> dict:
    if not path.exists():
        return dict(DEFAULT_POLICY)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    policy = dict(DEFAULT_POLICY)
    policy.update(loaded)
    return policy


def load_rates(path: str | None) -> dict:
    if not path:
        return {}
    table_path = Path(path)
    if not table_path.exists():
        return {}
    table = json.loads(table_path.read_text(encoding="utf-8"))
    min_samples = int(table.get("min_samples", 1))
    rates = {}
    for model_id, tasks in (table.get("rates") or {}).items():
        for task_type, cell in tasks.items():
            if int(cell.get("n", 0)) >= min_samples:
                rates[(model_id, task_type)] = float(cell["rate"])
    return rates


def build_quality_table(traces_path: Path, output_path: Path, min_samples: int = 1) -> dict:
    counts: dict[tuple[str, str], list[int]] = {}
    if traces_path.exists():
        for line in traces_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("status") != "completed" or record.get("include_in_quality") is False:
                continue
            if record.get("label") not in {"pass", "fail"}:
                continue
            model_id = record.get("returned_model_id")
            task_type = record.get("task_type")
            if not model_id or not task_type:
                continue
            counts.setdefault((model_id, task_type), []).append(1 if record["label"] == "pass" else 0)
    rates: dict[str, dict] = {}
    for (model_id, task_type), labels in counts.items():
        if len(labels) < min_samples:
            continue
        rates.setdefault(model_id, {})[task_type] = {
            "rate": round(sum(labels) / len(labels), 4),
            "n": len(labels),
        }
    table = {"min_samples": min_samples, "rates": rates}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(table, indent=2), encoding="utf-8")
    return table


def main() -> None:
    table = build_quality_table(Path("data/traces.jsonl"), Path("data/quality_table.json"))
    print(json.dumps(table, indent=2))


if __name__ == "__main__":
    main()
