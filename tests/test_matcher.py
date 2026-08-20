from bot.config import Match, Config, Limits, SkipRules, Subreddit, Rule
from bot.matcher import matches, first_match


def _cfg(rules):
    return Config(
        dry_run=True,
        review_required=True,
        disclosure="I am a bot.",
        limits=Limits(),
        skip=SkipRules(),
        subreddits=[Subreddit(name="test", enabled=True)],
        rules=rules,
        raw={},
    )


def test_any_of_word_boundary():
    m = Match(any_of=["api"])
    assert matches("how do I use the api?", m)[0] is True
    # "api" should not match inside "therapist"
    assert matches("I saw a therapist today", m)[0] is False


def test_none_of_vetoes():
    m = Match(any_of=["python"], none_of=["not python"])
    assert matches("I love python", m)[0] is True
    assert matches("this is not python code", m)[0] is False


def test_all_of_requires_every_term():
    m = Match(all_of=["reddit", "bot"])
    assert matches("a reddit bot", m)[0] is True
    assert matches("a reddit account", m)[0] is False


def test_empty_match_never_fires():
    m = Match()
    assert matches("anything at all", m)[0] is False


def test_regex_match():
    m = Match(regex=r"error\s+\d+")
    assert matches("got error 429 today", m)[0] is True
    assert matches("no errors here", m)[0] is False


def test_case_insensitive_default():
    m = Match(any_of=["PRAW"])
    assert matches("using praw here", m)[0] is True


def test_first_match_respects_rule_order_and_subreddit():
    r1 = Rule(id="a", enabled=True, match=Match(any_of=["hello"]), responses=["hi"], subreddits=["other"])
    r2 = Rule(id="b", enabled=True, match=Match(any_of=["hello"]), responses=["hey"], subreddits=[])
    cfg = _cfg([r1, r2])
    result = first_match(cfg, "test", "well hello there")
    assert result is not None
    # r1 is restricted to r/other, so r2 should win in r/test
    assert result.rule.id == "b"


def test_disabled_rule_skipped():
    r = Rule(id="a", enabled=False, match=Match(any_of=["hello"]), responses=["hi"])
    cfg = _cfg([r])
    assert first_match(cfg, "test", "hello") is None
