from types import SimpleNamespace

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
    assert state["taste_profile"] == "мягкий без горечи"
    assert state["caffeine_pref"] == "низкий"
    assert state["vessel"] == "кружка"
    assert state["liked_teas"] == ["Лунцзин", "Би Ло Чунь"]
    assert state["profile_complete"] is True
