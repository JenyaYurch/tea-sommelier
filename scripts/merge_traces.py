"""Merge several traces_*.json files (from sliced `eval generate` runs) into one.

Usage:
  uv run python scripts/merge_traces.py <out.json> <in1.json> <in2.json> ...

Example:
  uv run python scripts/merge_traces.py artifacts/traces/traces_merged.json .tmp_eval/traces/traces_*.json
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "Usage: uv run python scripts/merge_traces.py <out.json> <in1.json> ...",
            file=sys.stderr,
        )
        return 2
    out_path = Path(sys.argv[1])
    inputs: list[str] = []
    for arg in sys.argv[2:]:
        inputs.extend(sorted(glob.glob(arg)))
    if not inputs:
        print("No input traces matched", file=sys.stderr)
        return 1

    cases: list[dict] = []
    seen: set[str] = set()
    for name in inputs:
        data = json.loads(Path(name).read_text(encoding="utf-8"))
        for case in data.get("eval_cases") or []:
            case_id = case.get("eval_case_id") or ""
            if case_id in seen:
                continue
            seen.add(case_id)
            cases.append(case)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"eval_cases": cases}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Merged {len(cases)} case(s) from {len(inputs)} file(s) -> {out_path}")
    for case in cases:
        print(f"  · {case.get('eval_case_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
