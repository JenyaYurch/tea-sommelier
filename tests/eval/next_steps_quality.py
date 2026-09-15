"""Deterministic metric: next-step chips + catalog-only buy URLs (TEA-7)."""

from tea_agent.next_steps import (
    ACTION_LABELS,
    extract_response_text,
    invented_buy_urls,
    is_catalog_url,
    parse_next_steps,
    prompt_needs_next_steps,
)


def evaluate(instance):
    prompt = str(instance.get("prompt") or "")
    text = extract_response_text(instance.get("response"))
    steps = parse_next_steps(text)
    actions = {step.label.lower() for step in steps if step.kind == "action"}
    buys = [step for step in steps if step.kind == "buy"]
    buy_urls = [step.url for step in buys if step.url]
    invented = invented_buy_urls(text)
    needs = prompt_needs_next_steps(prompt)
    has_actions = set(ACTION_LABELS) <= actions
    catalog_buys = all(is_catalog_url(url) for url in buy_urls)

    if invented:
        return {
            "score": 2,
            "explanation": (
                "Invented or off-catalog buy URL(s): " + ", ".join(invented[:5])
            ),
        }
    if needs and not has_actions:
        return {
            "score": 1,
            "explanation": (
                "Recommendation/shop reply is missing the next-step chips "
                f"{list(ACTION_LABELS)} under '### Что дальше'."
            ),
        }
    if buy_urls and not catalog_buys:
        return {
            "score": 2,
            "explanation": "A Купить link is not a teashop.by catalog product_url.",
        }
    if needs and has_actions and not buy_urls and "[купить]" not in text.lower():
        return {
            "score": 3,
            "explanation": (
                "Action chips are present but there is no Купить chip or catalog URL."
            ),
        }
    return {
        "score": 5,
        "explanation": (
            "Next-step chips present with catalog-only buy links."
            if needs
            else "No invented shop URLs; next-steps not required for this prompt."
        ),
    }
