"""Agent Platform Memory Bank topics for tea-sommelier.

Used when creating/updating the Agent Engine instance that backs
VertexAiMemoryBankService. Cloud Run does not create this on startup —
run ``uv run python scripts/setup_memory_bank.py``.
"""

from __future__ import annotations

from google.genai import types
from vertexai._genai.types import ManagedTopicEnum, MemoryTopicId
from vertexai._genai.types import MemoryBankCustomizationConfig as CustomizationConfig
from vertexai._genai.types import (
    MemoryBankCustomizationConfigConsolidationConfig as ConsolidationConfig,
)
from vertexai._genai.types import (
    MemoryBankCustomizationConfigGenerateMemoriesExample as GenerateMemoriesExample,
)
from vertexai._genai.types import (
    MemoryBankCustomizationConfigGenerateMemoriesExampleConversationSource as ConversationSource,
)
from vertexai._genai.types import (
    MemoryBankCustomizationConfigGenerateMemoriesExampleConversationSourceEvent as ConversationEvent,
)
from vertexai._genai.types import (
    MemoryBankCustomizationConfigGenerateMemoriesExampleGeneratedMemory as GeneratedMemory,
)
from vertexai._genai.types import (
    MemoryBankCustomizationConfigMemoryTopic as MemoryTopic,
)
from vertexai._genai.types import (
    MemoryBankCustomizationConfigMemoryTopicCustomMemoryTopic as CustomMemoryTopic,
)
from vertexai._genai.types import (
    MemoryBankCustomizationConfigMemoryTopicManagedMemoryTopic as ManagedMemoryTopic,
)
from vertexai._genai.types import (
    ReasoningEngineContextSpecMemoryBankConfig as MemoryBankConfig,
)
from vertexai._genai.types import (
    ReasoningEngineContextSpecMemoryBankConfigTtlConfig as TtlConfig,
)

TEA_TASTE_TOPIC = "TEA_TASTE_PROFILE"
_YEAR_SECONDS = 365 * 24 * 60 * 60


def _user_event(text: str) -> ConversationEvent:
    return ConversationEvent(
        content=types.Content(role="user", parts=[types.Part.from_text(text=text)])
    )


memory_bank_config = MemoryBankConfig(
    ttl_config=TtlConfig(
        memory_revision_default_ttl=f"{_YEAR_SECONDS}s",
    ),
    customization_configs=[
        CustomizationConfig(
            memory_topics=[
                MemoryTopic(
                    managed_memory_topic=ManagedMemoryTopic(
                        managed_topic_enum=ManagedTopicEnum.USER_PREFERENCES,
                    ),
                ),
                MemoryTopic(
                    managed_memory_topic=ManagedMemoryTopic(
                        managed_topic_enum=ManagedTopicEnum.EXPLICIT_INSTRUCTIONS,
                    ),
                ),
                MemoryTopic(
                    custom_memory_topic=CustomMemoryTopic(
                        label=TEA_TASTE_TOPIC,
                        description=(
                            "Chinese green tea taste profile: experience level, "
                            "preferred taste notes, bitterness, brewing vessel, "
                            "caffeine preference, budget band, liked or disliked "
                            "cultivars. Ignore medical claims, prices, and "
                            "one-off questions that are not preferences."
                        ),
                    ),
                ),
            ],
            consolidation_config=ConsolidationConfig(
                revisions_per_candidate_count=10,
            ),
            generate_memories_examples=[
                GenerateMemoriesExample(
                    conversation_source=ConversationSource(
                        events=[
                            _user_event(
                                "Я новичок, хочу мягкий чай без горечи утром, "
                                "завариваю в кружке"
                            )
                        ],
                    ),
                    generated_memories=[
                        GeneratedMemory(
                            fact=(
                                "User is a beginner who prefers mild Chinese "
                                "green tea without bitterness for the morning "
                                "and brews in a mug."
                            ),
                            topics=[
                                MemoryTopicId(custom_memory_topic_label=TEA_TASTE_TOPIC),
                                MemoryTopicId(
                                    managed_memory_topic=ManagedTopicEnum.USER_PREFERENCES
                                ),
                            ],
                        )
                    ],
                ),
                GenerateMemoriesExample(
                    conversation_source=ConversationSource(
                        events=[
                            _user_event(
                                "Запомни: не предлагай Би Ло Чунь, люблю Лунцзин"
                            )
                        ],
                    ),
                    generated_memories=[
                        GeneratedMemory(
                            fact=(
                                "User asked the agent to remember they like "
                                "Longjing and do not want Biluochun."
                            ),
                            topics=[
                                MemoryTopicId(custom_memory_topic_label=TEA_TASTE_TOPIC),
                                MemoryTopicId(
                                    managed_memory_topic=ManagedTopicEnum.EXPLICIT_INSTRUCTIONS
                                ),
                            ],
                        )
                    ],
                ),
            ],
            enable_third_person_memories=False,
        )
    ],
    disable_memory_revisions=False,
)
