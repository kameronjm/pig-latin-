"""Rate limiting and politeness gates.

`allow_post` is the single choke point every reply passes through before it is
posted. It answers a plain question: is the bot allowed to post right now, in
this subreddit, to this author? If not, it says why.

Keeping all of this in one place means the scanner, the auto-poster, and the
review-queue poster all obey exactly the same limits.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .config import Config
from .db import Database


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


def _in_quiet_hours(config: Config, now: float) -> bool:
    start = config.limits.quiet_hours_utc_start
    end = config.limits.quiet_hours_utc_end
    if start is None or end is None or start == end:
        return False
    hour = time.gmtime(now).tm_hour
    if start < end:
        return start <= hour < end
    # Window wraps past midnight (e.g. 22 -> 6).
    return hour >= start or hour < end


def allow_post(
    config: Config,
    db: Database,
    subreddit: str,
    author: str | None,
    now: float | None = None,
) -> Decision:
    now = now if now is not None else time.time()
    lim = config.limits
    day_ago = now - 86400

    if _in_quiet_hours(config, now):
        return Decision(False, "within configured quiet hours")

    # Global spacing between posts.
    last = db.last_reply_ts()
    if last is not None:
        elapsed = now - last
        if elapsed < lim.min_seconds_between_replies:
            wait = int(lim.min_seconds_between_replies - elapsed)
            return Decision(False, f"global throttle: {wait}s until next post allowed")

    # Global daily cap.
    total_today = db.count_replies_since(day_ago)
    if total_today >= lim.max_replies_total_per_day:
        return Decision(False, f"daily total cap reached ({lim.max_replies_total_per_day})")

    # Per-subreddit daily cap (subreddit override or global default).
    sub_cfg = config.subreddit(subreddit)
    sub_cap = (
        sub_cfg.max_replies_per_day
        if sub_cfg and sub_cfg.max_replies_per_day is not None
        else lim.max_replies_per_subreddit_per_day
    )
    sub_today = db.count_replies_since(day_ago, subreddit=subreddit)
    if sub_today >= sub_cap:
        return Decision(False, f"per-subreddit daily cap reached for r/{subreddit} ({sub_cap})")

    # Per-user cooldown.
    if author and lim.per_user_cooldown_hours > 0:
        last_user = db.last_reply_ts_for_user(author)
        if last_user is not None:
            cooldown = lim.per_user_cooldown_hours * 3600
            if now - last_user < cooldown:
                return Decision(False, f"per-user cooldown active for u/{author}")

    return Decision(True, "ok")
