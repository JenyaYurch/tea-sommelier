"""A small robots.txt reader. Longest match wins, and a tie allows the path."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class _Rule:
    allow: bool
    length: int
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class _Group:
    agents: tuple[str, ...]
    rules: tuple[_Rule, ...]
    delay: float | None


def _compile(rule: str) -> re.Pattern[str]:
    parts: list[str] = []
    last = len(rule) - 1
    for index, char in enumerate(rule):
        if char == "*":
            parts.append(".*")
        elif char == "$" and index == last:
            parts.append("$")
        else:
            parts.append(re.escape(char))
    return re.compile("^" + "".join(parts))


def _groups(text: str) -> tuple[_Group, ...]:
    records: list[dict] = []
    current: dict = {"agents": [], "rules": [], "delay": None}

    def flush() -> None:
        nonlocal current
        if current["agents"]:
            records.append(current)
        current = {"agents": [], "rules": [], "delay": None}

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            flush()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if current["rules"] or current["delay"] is not None:
                flush()
            agent = value.lower() or "*"
            current["agents"].append(agent)
            continue
        if not current["agents"]:
            continue
        if key == "disallow" and value:
            current["rules"].append(_Rule(False, len(value), _compile(value)))
        elif key == "allow" and value:
            current["rules"].append(_Rule(True, len(value), _compile(value)))
        elif key == "crawl-delay":
            try:
                current["delay"] = float(value.replace(",", "."))
            except ValueError:
                continue
    flush()
    return tuple(
        _Group(tuple(record["agents"]), tuple(record["rules"]), record["delay"])
        for record in records
    )


def _target(path: str) -> str:
    if "://" in path:
        parts = urlsplit(path)
        if parts.query:
            return f"{parts.path}?{parts.query}"
        return parts.path or "/"
    return path if path.startswith("/") else f"/{path}"


def _select(text: str, user_agent: str) -> _Group | None:
    groups = _groups(text)
    ua = (user_agent or "*").lower()
    star: _Group | None = None
    best: _Group | None = None
    best_len = -1
    for group in groups:
        for agent in group.agents:
            if agent == "*":
                if star is None:
                    star = group
                continue
            if agent in ua and len(agent) > best_len:
                best = group
                best_len = len(agent)
    return best or star


def robots_allows(text: str, path: str, user_agent: str = "*") -> bool:
    """True when ``path`` may be fetched for this user agent."""
    group = _select(text or "", user_agent)
    if group is None or not group.rules:
        return True
    target = _target(path)
    matched = [rule for rule in group.rules if rule.pattern.search(target)]
    if not matched:
        return True
    longest = max(rule.length for rule in matched)
    top = [rule for rule in matched if rule.length == longest]
    return any(rule.allow for rule in top)


def crawl_delay(text: str, user_agent: str = "*") -> float | None:
    """Crawl-delay in seconds for the matching group, if the file sets one."""
    group = _select(text or "", user_agent)
    if group is None:
        return None
    return group.delay
