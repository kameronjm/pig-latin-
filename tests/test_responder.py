import pytest

from bot.config import Config, Limits, SkipRules, Subreddit, Rule, Match
from bot.responder import build_reply, choose_variant, MissingDisclosureError


def _cfg(disclosure="I am a bot.", sub_disclosure=None):
    return Config(
        dry_run=True,
        review_required=True,
        disclosure=disclosure,
        limits=Limits(),
        skip=SkipRules(),
        subreddits=[Subreddit(name="test", enabled=True, disclosure=sub_disclosure)],
        rules=[],
        raw={},
    )


def _rule(responses):
    return Rule(id="r", enabled=True, match=Match(any_of=["x"]), responses=responses)


def test_disclosure_always_appended():
    cfg = _cfg(disclosure="BEEP I am a bot.")
    body = build_reply(cfg, _rule(["Hello there"]), "test", 0)
    assert "Hello there" in body
    assert body.strip().endswith("BEEP I am a bot.")


def test_variants_rotate():
    rule = _rule(["one", "two", "three"])
    assert choose_variant(rule, 0) == "one"
    assert choose_variant(rule, 1) == "two"
    assert choose_variant(rule, 3) == "one"


def test_subreddit_disclosure_override():
    cfg = _cfg(disclosure="global", sub_disclosure="sub-specific bot note")
    body = build_reply(cfg, _rule(["hi"]), "test", 0)
    assert "sub-specific bot note" in body
    assert "global" not in body


def test_empty_disclosure_raises():
    # Build a config that bypasses load-time validation, then ensure build_reply
    # still refuses to produce an anonymous reply.
    cfg = _cfg(disclosure="   ")
    with pytest.raises(MissingDisclosureError):
        build_reply(cfg, _rule(["hi"]), "test", 0)
