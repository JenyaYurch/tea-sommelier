"""Brewing sub-agent: precise steeping from tea.support cards."""

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai import types

from tea_agent.tools import get_tea_card, resolve_tea

MODEL = "gemini-3.6-flash"

BREWING_INSTRUCTION = """
Ты — специалист по заварке зелёного китайского чая.
Отвечай по-русски. Факты о температуре/граммовке/времени — ТОЛЬКО из get_tea_card.

Порядок:
1) Если назван сорт — resolve_tea → возьми лучший slug.
2) get_tea_card(slug) и дай точную заварку из карточки: температура, граммовка,
   время, проливы (если есть), тип посуды.
3) Учти сосуд пользователя, если известен из контекста ({vessel?}): кружка vs гайвань.
4) Добавь 1–2 частые ошибки (слишком горячая вода, передержка) — без выдуманных цифр.
5) Если slug не найден — один уточняющий вопрос, не выдумывай параметры.

Не называй цены магазина. Не давай медицинских советов.
После ответа можно вернуть управление родителю tea_sommelier, если нужны рекомендации.
"""

brewing_agent = Agent(
    name="brewing_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=BREWING_INSTRUCTION,
    description=(
        "Gives precise Chinese-green brewing guides (temp, grams, time, vessel) "
        "using resolve_tea and get_tea_card."
    ),
    tools=[resolve_tea, get_tea_card],
)
