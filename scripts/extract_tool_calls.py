"""Extract actual tool calls from an eval trace case."""

from __future__ import annotations

import json
from pathlib import Path

TRACE = Path("artifacts/traces/traces_20260908_215121.json")
OUT = Path("artifacts/eval_debug/_tool_calls.json")


def collect_calls(obj, calls: list[dict], path: str = "") -> None:
    if isinstance(obj, dict):
        fc = obj.get("functionCall") or obj.get("function_call")
        if isinstance(fc, dict) and fc.get("name"):
            calls.append({"name": fc.get("name"), "args": fc.get("args"), "path": path})
        # ADK sometimes uses "name"+"args" under tool call wrappers
        if "name" in obj and "args" in obj and path.endswith("functionCall"):
            calls.append({"name": obj.get("name"), "args": obj.get("args"), "path": path})
        for key, value in obj.items():
            collect_calls(value, calls, f"{path}.{key}" if path else key)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            collect_calls(value, calls, f"{path}[{i}]")


def main() -> None:
    data = json.loads(TRACE.read_text(encoding="utf-8"))
    out = []
    for case in data["eval_cases"]:
        calls: list[dict] = []
        collect_calls(case.get("agent_data") or {}, calls)
        # dedupe preserving order
        seen = set()
        unique = []
        for call in calls:
            key = (call["name"], json.dumps(call.get("args"), sort_keys=True, ensure_ascii=False))
            if key in seen:
                continue
            seen.add(key)
            unique.append({"name": call["name"], "args": call.get("args")})
        out.append({"id": case.get("eval_case_id"), "tool_calls": unique})
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
