"""Summarize eval traces without needing Vertex grading."""

from __future__ import annotations

import json
import re
from pathlib import Path

TRACE = Path("artifacts/traces/traces_20260908_215121.json")
OUT = Path("artifacts/traces/_summary.json")

TOOL_RE = re.compile(
    r"(resolve_tea|search_teas|get_tea_card|similar_teas|compare_teas|"
    r"find_in_shop|ask_sommelier|save_taste_profile|onboarding_agent|brewing_agent)"
)


def _walk_texts(obj, texts: list[str]) -> None:
    if isinstance(obj, dict):
        text = obj.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text)
        for value in obj.values():
            _walk_texts(value, texts)
    elif isinstance(obj, list):
        for value in obj:
            _walk_texts(value, texts)


def main() -> None:
    data = json.loads(TRACE.read_text(encoding="utf-8"))
    summaries = []
    for case in data["eval_cases"]:
        prompt = case.get("prompt") or {}
        parts = prompt.get("parts") or [] if isinstance(prompt, dict) else []
        prompt_text = parts[0].get("text", "") if parts else ""
        agent = case.get("agent_data") or {}
        blob = json.dumps(agent, ensure_ascii=False)
        tools = sorted(set(TOOL_RE.findall(blob)))
        texts: list[str] = []
        _walk_texts(agent, texts)
        response = case.get("response") or case.get("final_response") or ""
        if not response and texts:
            response = texts[-1]
        summaries.append(
            {
                "id": case.get("eval_case_id"),
                "prompt": prompt_text,
                "tools": tools,
                "response_len": len(response or ""),
                "response_preview": (response or "")[:800],
                "case_keys": sorted(case.keys()),
                "agent_keys": sorted(agent.keys())
                if isinstance(agent, dict)
                else type(agent).__name__,
            }
        )
    OUT.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({len(summaries)} cases)")


if __name__ == "__main__":
    main()
