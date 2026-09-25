import json
from pathlib import Path

TRACE_PATH = Path("data/traces.jsonl")


def append_trace(record: dict, path: Path = TRACE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def label_last(label: str, path: Path = TRACE_PATH) -> bool:
    if label not in {"pass", "fail"} or not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if not lines[index].strip():
            continue
        record = json.loads(lines[index])
        if record.get("status") != "completed":
            continue
        record["label"] = label
        lines[index] = json.dumps(record, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    return False
