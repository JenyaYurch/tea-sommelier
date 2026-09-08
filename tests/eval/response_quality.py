"""Local LLM-as-judge for `custom_response_quality` (see eval_config.yaml)."""

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
        "Penalize empty replies, hallucinated cultivars, or medical advice.\n"
    )
    reference = instance.get("reference")
    if reference:
        rubric += (
            " The response should agree with the expected answer below; penalize "
            "factual disagreement with it."
        )
    prompt = (
        f"You are an expert QA evaluator for a Chinese-green-tea sommelier. {rubric}\n"
        f"User Prompt: {prompt_text}\n"
        f"Final Response: {instance.get('response', '')}\n"
    )
    if reference:
        prompt += f"Expected Answer (ground truth): {reference}\n"
    prompt += f"Full Agent Trace: {instance.get('agent_data', '')}\n"

    client = genai.Client()  # AI Studio (GEMINI_API_KEY) or Agent Platform (ADC)
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,  # deterministic grading
            response_mime_type="application/json",
            response_schema=_Verdict,  # guaranteed schema-valid JSON
        ),
    )
    verdict = response.parsed
    if verdict is None:  # model returned nothing usable
        return {"score": 0, "explanation": response.text or ""}
    return {"score": max(1, min(5, verdict.score)), "explanation": verdict.explanation}
