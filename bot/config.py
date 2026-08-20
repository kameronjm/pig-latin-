"""Load and validate configuration.

Two sources:
  * Secrets (API keys, passwords) come from environment variables / .env.
  * Behavior (subreddits, keywords, limits) comes from a YAML config file.

The split keeps credentials out of the config file that describes what the bot
does, so the behavior file can be committed/shared without leaking anything.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

try:  # optional in production (env may already be set), handy in dev
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is a convenience only
    pass


DEFAULT_CONFIG_PATH = os.environ.get("BOT_CONFIG", "config.yaml")
DEFAULT_DB_PATH = os.environ.get("BOT_DB", "data/bot.db")


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass
class Secrets:
    client_id: str
    client_secret: str
    username: str
    password: str
    user_agent: str

    @property
    def is_complete(self) -> bool:
        return all(
            [
                self.client_id,
                self.client_secret,
                self.username,
                self.password,
                self.user_agent,
            ]
        )


def load_secrets() -> Secrets:
    return Secrets(
        client_id=os.environ.get("REDDIT_CLIENT_ID", ""),
        client_secret=os.environ.get("REDDIT_CLIENT_SECRET", ""),
        username=os.environ.get("REDDIT_USERNAME", ""),
        password=os.environ.get("REDDIT_PASSWORD", ""),
        user_agent=os.environ.get("REDDIT_USER_AGENT", ""),
    )


@dataclass
class Limits:
    min_seconds_between_replies: int = 120
    max_replies_per_subreddit_per_day: int = 10
    max_replies_total_per_day: int = 40
    per_user_cooldown_hours: int = 24
    max_comment_age_hours: int = 24
    min_comment_score: int = 1
    quiet_hours_utc_start: int | None = None
    quiet_hours_utc_end: int | None = None


@dataclass
class SkipRules:
    users: list[str] = field(default_factory=list)
    skip_bot_authors: bool = True
    skip_locked_or_archived: bool = True

    def normalized_users(self) -> set[str]:
        return {u.strip().lower() for u in self.users if u.strip()}


@dataclass
class Subreddit:
    name: str
    enabled: bool = True
    disclosure: str | None = None
    max_replies_per_day: int | None = None


@dataclass
class Match:
    any_of: list[str] = field(default_factory=list)
    all_of: list[str] = field(default_factory=list)
    none_of: list[str] = field(default_factory=list)
    regex: str | None = None
    case_sensitive: bool = False


@dataclass
class Rule:
    id: str
    enabled: bool
    match: Match
    responses: list[str]
    subreddits: list[str] = field(default_factory=list)


@dataclass
class Config:
    dry_run: bool
    review_required: bool
    disclosure: str
    limits: Limits
    skip: SkipRules
    subreddits: list[Subreddit]
    rules: list[Rule]
    raw: dict[str, Any] = field(default_factory=dict)

    def enabled_subreddits(self) -> list[Subreddit]:
        return [s for s in self.subreddits if s.enabled]

    def subreddit(self, name: str) -> Subreddit | None:
        lname = name.lower()
        for s in self.subreddits:
            if s.name.lower() == lname:
                return s
        return None

    def is_allowed_subreddit(self, name: str) -> bool:
        s = self.subreddit(name)
        return bool(s and s.enabled)

    def disclosure_for(self, subreddit_name: str) -> str:
        s = self.subreddit(subreddit_name)
        if s and s.disclosure:
            return s.disclosure.strip()
        return self.disclosure.strip()


def _require(d: dict, key: str, path: str) -> Any:
    if key not in d:
        raise ConfigError(f"Missing required key '{key}' in {path}")
    return d[key]


def load_config(path: str | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(path):
        raise ConfigError(
            f"Config file not found at '{path}'. Copy config.example.yaml to "
            f"config.yaml and edit it (or set BOT_CONFIG)."
        )
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    disclosure = (data.get("disclosure") or "").strip()
    if not disclosure:
        raise ConfigError(
            "`disclosure` is required and must be non-empty. Every reply must "
            "identify the bot; the platform refuses to run without it."
        )

    limits_raw = data.get("limits") or {}
    limits = Limits(
        min_seconds_between_replies=int(limits_raw.get("min_seconds_between_replies", 120)),
        max_replies_per_subreddit_per_day=int(limits_raw.get("max_replies_per_subreddit_per_day", 10)),
        max_replies_total_per_day=int(limits_raw.get("max_replies_total_per_day", 40)),
        per_user_cooldown_hours=int(limits_raw.get("per_user_cooldown_hours", 24)),
        max_comment_age_hours=int(limits_raw.get("max_comment_age_hours", 24)),
        min_comment_score=int(limits_raw.get("min_comment_score", 1)),
        quiet_hours_utc_start=limits_raw.get("quiet_hours_utc_start"),
        quiet_hours_utc_end=limits_raw.get("quiet_hours_utc_end"),
    )

    skip_raw = data.get("skip") or {}
    skip = SkipRules(
        users=list(skip_raw.get("users") or []),
        skip_bot_authors=bool(skip_raw.get("skip_bot_authors", True)),
        skip_locked_or_archived=bool(skip_raw.get("skip_locked_or_archived", True)),
    )

    subreddits: list[Subreddit] = []
    for entry in data.get("subreddits") or []:
        name = _require(entry, "name", path)
        subreddits.append(
            Subreddit(
                name=str(name).lstrip("r/").strip("/ "),
                enabled=bool(entry.get("enabled", True)),
                disclosure=entry.get("disclosure"),
                max_replies_per_day=entry.get("max_replies_per_day"),
            )
        )
    if not subreddits:
        raise ConfigError(
            "No subreddits configured. Add at least one under `subreddits:`. "
            "The bot only ever acts in subreddits you explicitly allow."
        )

    rules: list[Rule] = []
    for entry in data.get("rules") or []:
        rid = _require(entry, "id", path)
        m = entry.get("match") or {}
        # Validate a regex early so we fail fast rather than at post time.
        if m.get("regex"):
            try:
                re.compile(m["regex"])
            except re.error as exc:
                raise ConfigError(f"Rule '{rid}' has an invalid regex: {exc}") from exc
        responses = [r for r in (entry.get("responses") or []) if str(r).strip()]
        if not responses:
            raise ConfigError(f"Rule '{rid}' has no non-empty responses.")
        rules.append(
            Rule(
                id=str(rid),
                enabled=bool(entry.get("enabled", True)),
                match=Match(
                    any_of=list(m.get("any_of") or []),
                    all_of=list(m.get("all_of") or []),
                    none_of=list(m.get("none_of") or []),
                    regex=m.get("regex"),
                    case_sensitive=bool(m.get("case_sensitive", False)),
                ),
                responses=responses,
                subreddits=[str(s).lstrip("r/").strip("/ ") for s in (entry.get("subreddits") or [])],
            )
        )

    return Config(
        dry_run=bool(data.get("dry_run", True)),
        review_required=bool(data.get("review_required", True)),
        disclosure=disclosure,
        limits=limits,
        skip=skip,
        subreddits=subreddits,
        rules=rules,
        raw=data,
    )
