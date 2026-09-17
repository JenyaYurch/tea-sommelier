from types import SimpleNamespace

from google.adk.sessions.state import State

from tea_agent.profile_tools import save_taste_profile


def test_save_taste_profile_writes_session_state() -> None:
    state: dict = {}
    ctx = SimpleNamespace(state=state)

    result = save_taste_profile(
        experience="новичок",
        taste_profile="мягкий без горечи",
        budget="",
        caffeine_pref="низкий",
        vessel="кружка",
        liked_teas="Лунцзин, Би Ло Чунь",
        tool_context=ctx,  # type: ignore[arg-type]
    )

    assert result["status"] == "success"
    assert state["experience"] == "новичок"
    assert state["user:experience"] == "новичок"
    assert state["taste_profile"] == "мягкий без горечи"
    assert state["user:taste_profile"] == "мягкий без горечи"
    assert state["caffeine_pref"] == "низкий"
    assert state["user:caffeine_pref"] == "низкий"
    assert state["vessel"] == "кружка"
    assert state["user:vessel"] == "кружка"
    assert state["liked_teas"] == ["Лунцзин", "Би Ло Чунь"]
    assert state["user:liked_teas"] == ["Лунцзин", "Би Ло Чунь"]
    assert state["profile_complete"] is True
    assert state["user:profile_complete"] is True


def test_save_taste_profile_records_user_prefixed_delta() -> None:
    """ADK persists tool writes via EventActions.state_delta, not a raw dict."""
    delta: dict = {}
    ctx = SimpleNamespace(state=State({}, delta))
    save_taste_profile(
        experience="новичок",
        taste_profile="мягкий без горечи",
        budget="",
        caffeine_pref="низкий",
        vessel="кружка",
        liked_teas="Лунцзин",
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert delta["user:experience"] == "новичок"
    assert delta["experience"] == "новичок"
    assert delta["user:profile_complete"] is True
    assert "temp:" not in "".join(delta)
