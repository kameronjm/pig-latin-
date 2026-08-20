"""Keyword / rule matching.

Given a comment body and the configured rules, decide which rule (if any) fires.
Matching is word-boundary and case-insensitive by default, which avoids the
classic substring false positives (e.g. "class" matching inside "classic").
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Config, Match, Rule


@dataclass
class MatchResult:
    rule: Rule
    matched_terms: list[str]


def _term_pattern(term: str, case_sensitive: bool) -> re.Pattern:
    flags = 0 if case_sensitive else re.IGNORECASE
    # Word-ish boundaries around the term. Terms may contain spaces/punctuation,
    # so we anchor on non-word transitions rather than strict \b on both ends.
    escaped = re.escape(term.strip())
    return re.compile(rf"(?<!\w){escaped}(?!\w)", flags)


def _contains(text: str, term: str, case_sensitive: bool) -> bool:
    if not term.strip():
        return False
    return _term_pattern(term, case_sensitive).search(text) is not None


def matches(text: str, m: Match) -> tuple[bool, list[str]]:
    """Return (did_match, matched_terms) for a single Match spec."""
    if text is None:
        return False, []

    cs = m.case_sensitive
    matched: list[str] = []

    # none_of acts as a veto.
    for term in m.none_of:
        if _contains(text, term, cs):
            return False, []

    # all_of: every term must be present.
    for term in m.all_of:
        if not _contains(text, term, cs):
            return False, []
        matched.append(term)

    # regex: must match if provided.
    if m.regex:
        flags = 0 if cs else re.IGNORECASE
        if re.search(m.regex, text, flags) is None:
            return False, []
        matched.append(f"regex:{m.regex}")

    # any_of: at least one must be present (only enforced when the list is set).
    if m.any_of:
        hits = [t for t in m.any_of if _contains(text, t, cs)]
        if not hits:
            return False, []
        matched.extend(hits)

    # Nothing to match on at all is treated as a non-match, to avoid a rule that
    # accidentally fires on every comment.
    if not (m.any_of or m.all_of or m.regex):
        return False, []

    return True, matched


def first_match(config: Config, subreddit: str, text: str) -> MatchResult | None:
    """First enabled rule (in config order) whose match spec fires here."""
    lsub = subreddit.lower()
    for rule in config.rules:
        if not rule.enabled:
            continue
        if rule.subreddits and lsub not in {s.lower() for s in rule.subreddits}:
            continue
        ok, terms = matches(text, rule.match)
        if ok:
            return MatchResult(rule=rule, matched_terms=terms)
    return None
