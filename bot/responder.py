"""Build the reply body: pick a response variant and append the disclosure.

Two guarantees enforced here:
  * A non-empty disclosure footer is ALWAYS appended. `build_reply` raises if the
    disclosure is empty, so nothing anonymous can ever be posted.
  * Response variants rotate deterministically based on how many times the rule
    has already fired, so the bot doesn't paste identical text over and over.
"""
from __future__ import annotations

from .config import Config, Rule


class MissingDisclosureError(Exception):
    """Raised if we somehow try to build a reply without a disclosure footer."""


def choose_variant(rule: Rule, rotation_index: int) -> str:
    variants = rule.responses
    if not variants:
        raise ValueError(f"Rule '{rule.id}' has no responses")
    return variants[rotation_index % len(variants)]


def build_reply(config: Config, rule: Rule, subreddit: str, rotation_index: int) -> str:
    body = choose_variant(rule, rotation_index).strip()
    disclosure = config.disclosure_for(subreddit).strip()
    if not disclosure:
        raise MissingDisclosureError(
            "Refusing to build a reply with an empty disclosure footer."
        )
    return f"{body}\n\n---\n\n{disclosure}"
