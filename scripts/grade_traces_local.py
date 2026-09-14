"""Grade eval traces locally without Vertex / agents-cli eval grade.

Uses the same custom judge as tests/eval/response_quality.py (GEMINI_API_KEY).

Example:
  uv run python scripts/grade_traces_local.py artifacts/traces/traces_20260909_213601.json
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Ensure tests/eval is importable for response_quality
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "eval"))

from response_quality import evaluate  # noqa: E402


def _prompt_text(case: dict) -> str:
    prompt = case.get("prompt") or {}
    if isinstance(prompt, str):
        return prompt
    parts = prompt.get("parts") or []
    if parts and isinstance(parts[0], dict):
        return str(parts[0].get("text") or "")
    return ""


def _final_response(case: dict) -> str:
    for key in ("response", "final_response"):
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            return value
    responses = case.get("responses")
    if isinstance(responses, list) and responses:
        last = responses[-1]
        if isinstance(last, str):
            return last
        if isinstance(last, dict):
            parts = last.get("parts") or []
            texts = [
                p.get("text", "")
                for p in parts
                if isinstance(p, dict) and p.get("text")
            ]
            if texts:
                return "\n".join(texts)
    # Fallback: last text blob in agent_data
    texts: list[str] = []

    def walk(obj) -> None:
        if isinstance(obj, dict):
            text = obj.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(case.get("agent_data") or {})
    return texts[-1] if texts else ""


def main() -> int:
    load_dotenv(ROOT / ".env")
    # Windows consoles default to cp1252; judge explanations may contain
    # Cyrillic/symbols that would crash print() with UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    if len(sys.argv) < 2:
        print(
            "Usage: uv run python scripts/grade_traces_local.py <traces.json>",
            file=sys.stderr,
        )
        return 2

    traces_path = Path(sys.argv[1])
    data = json.loads(traces_path.read_text(encoding="utf-8"))
    cases = data.get("eval_cases") or []
    if not cases:
        print("No eval_cases in file", file=sys.stderr)
        return 1

    results = []
    print(f"Grading {len(cases)} case(s) from {traces_path.name}")
    for i, case in enumerate(cases):
        if i > 0:
            # Free-tier judge is ~5 req/min: pace calls to avoid 429.
            time.sleep(6)
        case_id = case.get("eval_case_id") or "?"
        instance = {
            "prompt": _prompt_text(case),
            "response": _final_response(case),
            "agent_data": case.get("agent_data") or {},
            "reference": case.get("reference"),
        }
        print(f"  · {case_id} ...", flush=True)
        verdict = None
        for attempt in range(4):
            try:
                verdict = evaluate(instance)
                break
            except Exception as exc:  # noqa: BLE001 — surface judge errors
                msg = f"{type(exc).__name__}: {exc}"
                # Retry only transient judge errors: 5xx (model overloaded) and
                # per-minute 429. PerDay quota 429 is pointless to retry.
                transient = "503" in msg or "500" in msg or "UNAVAILABLE" in msg
                per_minute_429 = "429" in msg and "PerMinute" in msg
                if (transient or per_minute_429) and attempt < 3:
                    wait = 15 * (attempt + 1)
                    print(f"    transient judge error, retry in {wait}s: {msg[:100]}")
                    time.sleep(wait)
                    continue
                verdict = {"score": 0, "explanation": msg}
                break
        row = {
            "eval_case_id": case_id,
            "score": verdict.get("score"),
            "explanation": verdict.get("explanation"),
            "response_preview": (instance["response"] or "")[:300],
        }
        results.append(row)
        print(f"    score={row['score']}  {str(row['explanation'])[:160]}")

    out_dir = ROOT / "artifacts" / "grade_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"local_results_{stamp}.json"
    payload = {
        "source_traces": str(traces_path).replace("\\", "/"),
        "metric": "custom_response_quality",
        "graded_at": stamp,
        "results": results,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
