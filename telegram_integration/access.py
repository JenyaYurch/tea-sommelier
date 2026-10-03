# ruff: noqa: RUF001
"""Closed-beta access gate and per-user rate limit for the Telegram bot (TEA-36).

Who may talk to tea-agent
--------------------------
The gate is enforced when ``TELEGRAM_ACCESS_MODE`` is ``closed`` (also
``enforce`` / ``on``), or when the mode is ``auto`` (the default) and either:

* the process is the Cloud Run webhook (``PORT`` and ``SERVICE_URL`` are set), or
* local polling has an allowlist or an invite code configured.

``auto`` with nothing configured stays open for local polling, so
``uv run python -m telegram_integration`` keeps working. Webhook mode with
nothing configured fails closed: every user gets a short Russian refusal and
tea-agent is not called. ``TELEGRAM_ACCESS_MODE=open`` turns the gate off
even on Cloud Run; the deploy spec does not do that.

Allowed when the gate is on:

* ``TELEGRAM_ADMIN_USER_IDS`` — always, even if the id is not on the allowlist
* ``TELEGRAM_ALLOWED_USER_IDS`` — read from the environment on startup
  (plain env or a Secret Manager secret mounted as that env var)
* users who redeemed ``TELEGRAM_INVITE_CODE`` with ``/start <code>`` in this process

Invite grants after a restart
-----------------------------
Allowlist and admin ids live in the process environment. They come back after
a restart, a scale-to-zero, or a new Cloud Run revision, as long as that
revision still has the variables.

Invite-code approvals live only in ``AccessGate.invited_user_ids`` (process
memory). They are dropped when this process exits. The code itself is still
valid: the tester sends ``/start <code>`` again. Nothing is written to disk
or to the ADK session store. Session memory and this set are independent;
both are empty after a restart. Copy redeemed ids from the log line
``invite redeemed by telegram user <id>`` into ``TELEGRAM_ALLOWED_USER_IDS``
when access should survive a restart. The beta service is one instance;
a second instance would not see the first instance's grants.

Rate limit
----------
Each allowed user gets ``TELEGRAM_RATE_LIMIT_PER_MINUTE`` messages (default 4)
and ``TELEGRAM_RATE_LIMIT_PER_DAY`` messages (default 30, UTC day). The check
runs before tea-agent for ordinary text and for inline-button callbacks.
``0`` disables that bucket. Counters are in-memory and reset when the process
restarts. Admins use the same caps: they share the one Gemini key.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

ENV_ACCESS_MODE = "TELEGRAM_ACCESS_MODE"
ENV_ALLOWED_USER_IDS = "TELEGRAM_ALLOWED_USER_IDS"
ENV_ADMIN_USER_IDS = "TELEGRAM_ADMIN_USER_IDS"
ENV_INVITE_CODE = "TELEGRAM_INVITE_CODE"
ENV_RATE_LIMIT_PER_MINUTE = "TELEGRAM_RATE_LIMIT_PER_MINUTE"
ENV_RATE_LIMIT_PER_DAY = "TELEGRAM_RATE_LIMIT_PER_DAY"

DEFAULT_RATE_LIMIT_PER_MINUTE = 4
DEFAULT_RATE_LIMIT_PER_DAY = 30

_OPEN_MODES = frozenset({"open", "off", "disabled"})
_CLOSED_MODES = frozenset({"closed", "enforce", "on"})
_ID_SPLIT = re.compile(r"[,;\s]+")

CLOSED_BETA_TEXT = (
    "Бот сейчас в закрытой бете и отвечает только приглашённым. "
    "Если вас должны были добавить, напишите владельцу."
)
CLOSED_BETA_WITH_INVITE_TEXT = (
    "Бот сейчас в закрытой бете. Если у вас есть код приглашения, "
    "отправьте его командой /start и код одним сообщением."
)
INVITE_REJECTED_TEXT = (
    "Код не подошёл. Отправьте /start и код одним сообщением, без лишних слов."
)
INVITE_ACCEPTED_TEXT = "Код принят, доступ открыт.\n\n"
RATE_LIMIT_MINUTE_TEXT = (
    "Слишком много сообщений за минуту. Подождите немного и напишите снова."
)
RATE_LIMIT_DAY_TEXT = (
    "На сегодня сообщений достаточно. Так мы бережём общий лимит. Напишите завтра."
)


def parse_user_ids(raw: str | None) -> frozenset[int]:
    """Parse Telegram user ids separated by commas, semicolons, or whitespace."""
    ids: set[int] = set()
    for part in _ID_SPLIT.split((raw or "").strip()):
        if not part:
            continue
        if not part.isdigit():
            logger.warning("ignoring telegram user id %r (digits only)", part)
            continue
        ids.add(int(part))
    return frozenset(ids)


def _parse_limit(raw: str | None, *, default: int, name: str) -> int:
    text = (raw or "").strip()
    if not text:
        return default
    if text.isdigit():
        return int(text)
    logger.warning("invalid %s=%r; using %s", name, raw, default)
    return default


def access_is_enforced(
    mode: str | None,
    *,
    webhook: bool,
    has_allowlist: bool,
    has_invite: bool,
) -> bool:
    """Return whether unknown users must be refused.

    ``open`` never enforces. ``closed`` always enforces. ``auto`` enforces on
    the webhook, and on local polling only when an allowlist or invite code
    is set. Admins alone do not turn the local gate on.
    """
    normalized = (mode or "").strip().lower() or "auto"
    if normalized in _OPEN_MODES:
        return False
    if normalized in _CLOSED_MODES:
        return True
    if normalized != "auto":
        logger.warning("unknown %s=%r; using auto", ENV_ACCESS_MODE, mode)
    if webhook:
        return True
    return has_allowlist or has_invite


@dataclass(frozen=True)
class AccessConfig:
    enforced: bool
    allowed_user_ids: frozenset[int]
    admin_user_ids: frozenset[int]
    invite_code: str
    rate_limit_per_minute: int
    rate_limit_per_day: int

    @classmethod
    def fail_closed(cls) -> AccessConfig:
        return cls(
            enforced=True,
            allowed_user_ids=frozenset(),
            admin_user_ids=frozenset(),
            invite_code="",
            rate_limit_per_minute=DEFAULT_RATE_LIMIT_PER_MINUTE,
            rate_limit_per_day=DEFAULT_RATE_LIMIT_PER_DAY,
        )


def load_access_config(*, webhook: bool) -> AccessConfig:
    """Read the beta gate from the environment."""
    allowed = parse_user_ids(os.getenv(ENV_ALLOWED_USER_IDS))
    admins = parse_user_ids(os.getenv(ENV_ADMIN_USER_IDS))
    invite = os.getenv(ENV_INVITE_CODE, "").strip()
    return AccessConfig(
        enforced=access_is_enforced(
            os.getenv(ENV_ACCESS_MODE),
            webhook=webhook,
            has_allowlist=bool(allowed),
            has_invite=bool(invite),
        ),
        allowed_user_ids=allowed,
        admin_user_ids=admins,
        invite_code=invite,
        rate_limit_per_minute=_parse_limit(
            os.getenv(ENV_RATE_LIMIT_PER_MINUTE),
            default=DEFAULT_RATE_LIMIT_PER_MINUTE,
            name=ENV_RATE_LIMIT_PER_MINUTE,
        ),
        rate_limit_per_day=_parse_limit(
            os.getenv(ENV_RATE_LIMIT_PER_DAY),
            default=DEFAULT_RATE_LIMIT_PER_DAY,
            name=ENV_RATE_LIMIT_PER_DAY,
        ),
    )


def invite_codes_match(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    reason: str = ""

    @property
    def user_text(self) -> str:
        if self.reason == "day":
            return RATE_LIMIT_DAY_TEXT
        return RATE_LIMIT_MINUTE_TEXT


class RateLimiter:
    """Fixed windows: unix minute, and UTC calendar day.

    ``consume`` is synchronous. python-telegram-bot runs handlers on one
    asyncio loop, so the check is not interleaved mid-update.
    A limit of ``0`` turns that window off.
    """

    def __init__(
        self,
        per_minute: int,
        per_day: int,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.per_minute = per_minute
        self.per_day = per_day
        self._now = now or (lambda: datetime.now(UTC))
        self._minute: dict[int, tuple[int, int]] = {}
        self._day: dict[int, tuple[str, int]] = {}

    def consume(self, user_id: int) -> RateDecision:
        moment = self._now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        else:
            moment = moment.astimezone(UTC)
        uid = int(user_id)
        minute_id = int(moment.timestamp() // 60)
        day_key = moment.date().isoformat()

        seen_minute, minute_count = self._minute.get(uid, (minute_id, 0))
        if seen_minute != minute_id:
            minute_count = 0
        seen_day, day_count = self._day.get(uid, (day_key, 0))
        if seen_day != day_key:
            day_count = 0

        if self.per_day > 0 and day_count >= self.per_day:
            return RateDecision(False, "day")
        if self.per_minute > 0 and minute_count >= self.per_minute:
            return RateDecision(False, "minute")

        self._minute[uid] = (minute_id, minute_count + 1)
        self._day[uid] = (day_key, day_count + 1)
        return RateDecision(True)


@dataclass
class AccessGate:
    config: AccessConfig
    invited_user_ids: set[int] = field(default_factory=set)
    rate_limiter: RateLimiter | None = None

    def __post_init__(self) -> None:
        if self.rate_limiter is None:
            self.rate_limiter = RateLimiter(
                self.config.rate_limit_per_minute,
                self.config.rate_limit_per_day,
            )

    def is_allowed(self, user_id: int) -> bool:
        if not self.config.enforced:
            return True
        uid = int(user_id)
        if uid in self.config.admin_user_ids:
            return True
        if uid in self.config.allowed_user_ids:
            return True
        return uid in self.invited_user_ids

    def redeem_invite(self, user_id: int, code: str) -> bool:
        """Remember ``user_id`` until this process exits. False if the code differs."""
        if not invite_codes_match(code.strip(), self.config.invite_code):
            return False
        self.invited_user_ids.add(int(user_id))
        return True

    def consume_rate(self, user_id: int) -> RateDecision:
        assert self.rate_limiter is not None
        return self.rate_limiter.consume(user_id)

    def denied_text(self, *, code_rejected: bool = False) -> str:
        if code_rejected and self.config.invite_code:
            return INVITE_REJECTED_TEXT
        if self.config.invite_code:
            return CLOSED_BETA_WITH_INVITE_TEXT
        return CLOSED_BETA_TEXT


def get_access_gate(bot_data: dict) -> AccessGate:
    gate = bot_data.get("access_gate")
    if isinstance(gate, AccessGate):
        return gate
    logger.error("beta access gate is not configured; failing closed")
    return AccessGate(AccessConfig.fail_closed())
