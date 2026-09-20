"""Onboarding sub-agent: collect taste profile into session state."""

import os

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai import types

from tea_agent.profile_tools import save_taste_profile

MODEL = os.environ.get("TEA_AGENT_MODEL", "gemini-3.6-flash")

ONBOARDING_INSTRUCTION = """
Ты — онбординг-ассистент сомелье по китайскому чаю (зелёный, белый, жёлтый, красный, пуэр, GABA).
Говори по-русски. Задача: собрать профиль вкуса и сохранить его через save_taste_profile.

Если в первом сообщении уже есть опыт и вкус/вайб — сразу save_taste_profile
(неизвестные поля = "") и верни управление tea_sommelier для рекомендаций.
Иначе спрашивай по 1–2 пункта за ход, не стеной:
1) опыт (новичок / уже пил китайский чай / продвинутый)
2) вкус (мягкий / яркий / без горечи / цветочный / ореховый / свежий…)
3) бюджет (если назвал — сохрани; иначе можно пустую строку)
4) кофеин (низкий / средний / высокий / без предпочтений)
5) посуда (гайвань / кружка / чайник / неизвестно)

Когда собрал достаточно (хотя бы опыт + вкус), вызови save_taste_profile.
Для неизвестных полей передай пустую строку "". liked_teas — через запятую или "".
После успешного save коротко подтверди профиль и передай управление родителю tea_sommelier
(чтобы он рекомендовал чаи по профилю). Не вызывай tea.support tools — только анкета.
Не давай медицинских обещаний.
"""

onboarding_agent = Agent(
    name="onboarding_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=ONBOARDING_INSTRUCTION,
    description=(
        "Collects beginner taste profile (experience, taste, budget, caffeine, "
        "vessel) and saves it to session state via save_taste_profile."
    ),
    tools=[save_taste_profile],
)
