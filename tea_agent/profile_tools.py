"""Session taste-profile tools for onboarding."""

from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext

# ADK Cloud SQL codelab: ``user:`` keys survive new sessions for the same user_id.
# Unprefixed copies keep the current session's interpolations filled.


def _write_profile_key(state: Any, key: str, value: Any) -> None:
    state[key] = value
    state[f"user:{key}"] = value


def save_taste_profile(
    experience: str,
    taste_profile: str,
    budget: str,
    caffeine_pref: str,
    vessel: str,
    liked_teas: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Save the user's Chinese-green taste profile into session state.

    Call when onboarding has enough answers (empty string = unknown for a field).
    Pass liked_teas as a comma-separated list of names/slugs, or empty string.

    Args:
        experience: beginner / intermediate / advanced (or free text).
        taste_profile: Preferred taste notes (soft, floral, nutty, no bitterness…).
        budget: Budget band or currency note if given.
        caffeine_pref: low / medium / high / no preference.
        vessel: gaiwan / mug / teapot / unknown.
        liked_teas: Comma-separated liked teas, or empty.

    Returns:
        Dict confirming what was saved.
    """
    liked = [part.strip() for part in liked_teas.split(",") if part.strip()]
    profile = {
        "experience": experience.strip(),
        "taste_profile": taste_profile.strip(),
        "budget": budget.strip(),
        "caffeine_pref": caffeine_pref.strip(),
        "vessel": vessel.strip(),
        "liked_teas": liked,
    }
    for key, value in profile.items():
        _write_profile_key(tool_context.state, key, value)
    _write_profile_key(tool_context.state, "profile_complete", True)
    return {"status": "success", "saved": profile}
