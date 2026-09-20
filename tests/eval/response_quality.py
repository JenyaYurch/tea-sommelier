"""Local LLM-as-judge for `custom_response_quality` (see eval_config.yaml)."""

import time

from google import genai
from google.genai import types
from pydantic import BaseModel


class _Verdict(BaseModel):
    score: int  # 1-5
    explanation: str


def evaluate(instance):
    prompt_text = str(instance.get("prompt", ""))
    rubric = (
        "Grade the tea sommelier agent's final response on a 1-5 scale "
        "(1 poor, 5 excellent).\n"
        "Criteria:\n"
        "- Reply is in Russian.\n"
        "- Facts about taste/terroir/brewing come from tools, not invention.\n"
        "- No invented shop prices in BYN/EUR/USD outside tool results.\n"
        "- No medical promises (cure, detox, blood pressure treatment).\n"
        "- For comparisons of named teas: should use resolve/compare path, "
        "not ask_sommelier as primary.\n"
        "- For soft/beginner recommendation prompts: ideally ~3 teas with "
        "a clear 'why' for each.\n"
        "- For brewing how-to: concrete temp/grams/time grounded in tools "
        "or an honest clarification if data missing.\n"
        "- For price/shop/gift/partner-store prompts (e.g. 'gift under 20 euro', "
        "'what can I buy from the partner'): the agent must call find_in_shop; "
        "state prices only in BYN exactly as returned by the tool and include "
        "the product_url for each offered item; if find_in_shop returns "
        "not_found for a tea, no price or link may be invented for it. "
        "Recommending several in-budget gift options with links is ideal.\n"
        "- For 'only a mug' brewing prompts: mug-adapted guidance (cooler "
        "water / less leaf / fewer short steeps) grounded in get_tea_card "
        "data, or one honest clarifying question; no invented parameters.\n"
        "- For onboarding prompts (new user without stated experience/taste): "
        "the agent should collect the profile (route to onboarding_agent or "
        "ask 1-2 focused questions and/or save_taste_profile) instead of "
        "dumping generic recommendations.\n"
        "- For prompts that yield 3 recommendations or a partner-shop/gift "
        "list: the final reply must end with a '### Что дальше' block that "
        "lists next-step options мягче / дешевле / без горечи / подарок / "
        "подробнее, and Купить must be a markdown link to product_url from "
        "find_in_shop (teashop.by catalog) — never an invented URL.\n"
        "- For mixed-order / cart prompts that list several named teas: the agent "
        "must cover each named item (not refuse as green-only / out of specialty); "
        "must not force exactly 3 recommendations; facts of taste/terroir/temp "
        "only from tools; if a tea.support card is missing, say there is no data "
        "instead of inventing an encyclopedia entry.\n"
        "- GABA is not a tea.support tea_type: do not invent brewing numbers.\n"
        "- Brewing for shu/sheng/red/white must come from get_tea_card, not a "
        "green-tea 75–80 °C default.\n"
        "Penalize empty replies, hallucinated cultivars, or medical advice.\n"
    )
    reference = instance.get("reference")
    if reference:
        rubric += (
            " The response should agree with the expected answer below; penalize "
            "factual disagreement with it."
        )
    prompt = (
        f"You are an expert QA evaluator for a Chinese-tea sommelier "
        f"(green, white, yellow, red, puerh, GABA — not green-only). {rubric}\n"
        f"User Prompt: {prompt_text}\n"
        f"Final Response: {instance.get('response', '')}\n"
    )
    if reference:
        prompt += f"Expected Answer (ground truth): {reference}\n"
    prompt += f"Full Agent Trace: {instance.get('agent_data', '')}\n"

    # Hard per-attempt timeout: without it a stalled API call can hang the
    # whole `eval grade` run silently (observed on free-tier 503 spikes).
    client = genai.Client(
        http_options=types.HttpOptions(timeout=90_000)
    )  # AI Studio (GEMINI_API_KEY) or Agent Platform (ADC)
    # Judge models; the agent under test runs gemini-3.6-flash. Kept off the
    # agent's model so judge calls don't share its tiny free-tier daily quota.
    # Fallback: flash-lite when the primary judge is overloaded (503 spikes).
    response = None
    last_exc: Exception | None = None
    for model in ("gemini-3.5-flash", "gemini-3.1-flash-lite"):
        for attempt in range(3):
            try:
                print(f"[judge] {model} attempt {attempt + 1}", flush=True)
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0,  # deterministic grading
                        response_mime_type="application/json",
                        response_schema=_Verdict,  # guaranteed schema-valid JSON
                    ),
                )
                break
            except Exception as exc:  # noqa: BLE001 — retry/fallback judge errors
                last_exc = exc
                msg = str(exc)
                print(f"[judge] {model} failed: {msg[:120]}", flush=True)
                transient = (
                    "503" in msg
                    or "500" in msg
                    or "UNAVAILABLE" in msg
                    or ("429" in msg and "PerMinute" in msg)
                )
                if transient and attempt < 2:
                    time.sleep(15 * (attempt + 1))
                    continue
                break  # per-day quota / persistent error: try fallback model
        if response is not None:
            break
    if response is None:
        return {"score": 0, "explanation": f"judge unavailable: {last_exc}"}
    verdict = response.parsed
    if verdict is None:  # model returned nothing usable
        return {"score": 0, "explanation": response.text or ""}
    return {"score": max(1, min(5, verdict.score)), "explanation": verdict.explanation}
