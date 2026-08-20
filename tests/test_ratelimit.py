import time

from bot.config import Config, Limits, SkipRules, Subreddit
from bot.db import Database
from bot.ratelimit import allow_post


def _cfg(**limit_kwargs):
    return Config(
        dry_run=True,
        review_required=False,
        disclosure="I am a bot.",
        limits=Limits(**limit_kwargs),
        skip=SkipRules(),
        subreddits=[Subreddit(name="test", enabled=True, max_replies_per_day=None)],
        rules=[],
        raw={},
    )


def _db(tmp_path):
    return Database(path=str(tmp_path / "t.db"))


def _record(db, sub="test", author="alice", ts=None):
    db.record_reply(
        comment_id=f"c{time.time_ns()}",
        parent_id=None,
        subreddit=sub,
        author=author,
        rule_id="r",
        body="hi",
        status="posted",
    )


def test_allows_when_empty(tmp_path):
    cfg = _cfg()
    db = _db(tmp_path)
    assert allow_post(cfg, db, "test", "alice").allowed is True


def test_global_throttle_blocks(tmp_path):
    cfg = _cfg(min_seconds_between_replies=120)
    db = _db(tmp_path)
    _record(db, author="bob")
    d = allow_post(cfg, db, "test", "alice")
    assert d.allowed is False
    assert "throttle" in d.reason


def test_total_daily_cap(tmp_path):
    cfg = _cfg(min_seconds_between_replies=0, max_replies_total_per_day=2)
    db = _db(tmp_path)
    _record(db, author="a")
    _record(db, author="b")
    d = allow_post(cfg, db, "test", "c")
    assert d.allowed is False
    assert "daily total" in d.reason


def test_per_user_cooldown(tmp_path):
    cfg = _cfg(min_seconds_between_replies=0, per_user_cooldown_hours=24)
    db = _db(tmp_path)
    _record(db, author="repeat")
    d = allow_post(cfg, db, "test", "repeat")
    assert d.allowed is False
    assert "per-user" in d.reason


def test_quiet_hours(tmp_path):
    now = time.time()
    hour = time.gmtime(now).tm_hour
    cfg = _cfg(min_seconds_between_replies=0, quiet_hours_utc_start=hour, quiet_hours_utc_end=(hour + 1) % 24)
    db = _db(tmp_path)
    d = allow_post(cfg, db, "test", "alice", now=now)
    assert d.allowed is False
    assert "quiet" in d.reason
