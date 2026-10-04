"""Serialize one Telegram user's session work without a process-wide lock.

``concurrent_updates`` is on, and each account has a single ADK session.
Two fast taps would otherwise start two ``/run`` calls on that session.
Different users still run at the same time.

The map is per process. tea-agent and telegram-integration are pinned to
``max-instances=1``, so a second instance is not in the pilot. Idle locks are
dropped once the map reaches ``max_idle``; a lock that is held (or has
waiters, which implies it is held) stays, so an in-flight turn is never
split onto a second lock.
"""

from __future__ import annotations

import asyncio

MAX_IDLE_SESSION_LOCKS = 256


class SessionTurnGate:
    """One ``asyncio.Lock`` per Telegram user, with idle locks reclaimed."""

    def __init__(self, *, max_idle: int = MAX_IDLE_SESSION_LOCKS) -> None:
        if max_idle < 1:
            raise ValueError("max_idle must be at least 1")
        self._max_idle = max_idle
        self._locks: dict[int, asyncio.Lock] = {}

    def lock_for(self, telegram_user_id: int) -> asyncio.Lock:
        """Return the lock for this user. Caller must ``async with`` it immediately.

        There is no await between this return and ``Lock.acquire``, so another
        task cannot drop the lock before it is held. Do not await in between.
        """
        key = int(telegram_user_id)
        lock = self._locks.get(key)
        if lock is not None:
            return lock
        if len(self._locks) >= self._max_idle:
            self._drop_idle_locks()
        lock = asyncio.Lock()
        self._locks[key] = lock
        return lock

    def _drop_idle_locks(self) -> None:
        idle = [key for key, lock in self._locks.items() if not lock.locked()]
        for key in idle:
            del self._locks[key]


def get_turn_gate(bot_data: dict) -> SessionTurnGate:
    gate = bot_data.get("turn_gate")
    if isinstance(gate, SessionTurnGate):
        return gate
    gate = SessionTurnGate()
    bot_data["turn_gate"] = gate
    return gate
