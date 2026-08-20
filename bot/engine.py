"""The orchestration layer: scan subreddits, decide, and post.

Flow:
    scan()      -> for each allowed subreddit, look at recent comments, apply
                   skip rules + keyword matching, and either enqueue a candidate
                   for review or (if review isn't required) post it immediately.
    post_item() -> post one queued/approved candidate, honoring rate limits.
    run_once()  -> scan, then post everything approved & allowed right now.

Every path funnels through the same guardrails: allowlist check, dedup,
disclosure footer, and the rate-limit gate.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .config import Config
from .db import Database
from .matcher import first_match
from .ratelimit import allow_post
from .reddit_client import CommentView, RedditClient
from .responder import build_reply

log = logging.getLogger("bot.engine")


@dataclass
class ScanReport:
    scanned: int = 0
    matched: int = 0
    enqueued: int = 0
    posted: int = 0
    skipped: int = 0


def _looks_like_bot(author: str) -> bool:
    a = author.lower()
    return a.endswith("bot") or a.endswith("-bot") or a.endswith("_bot") or "autamod" in a


def should_skip(config: Config, db: Database, c: CommentView) -> str | None:
    """Return a human-readable reason to skip this comment, or None to proceed."""
    if not c.author:
        return "deleted/removed author"
    if config.is_allowed_subreddit(c.subreddit) is False:
        return f"subreddit r/{c.subreddit} not on allowlist"

    author_l = c.author.lower()
    if author_l == config.raw.get("_bot_username", "").lower():
        return "own comment"
    if author_l in config.skip.normalized_users():
        return "author on skip list"
    if config.skip.skip_bot_authors and _looks_like_bot(c.author):
        return "author looks like a bot"

    if config.skip.skip_locked_or_archived and (c.is_locked or c.is_archived):
        return "thread locked or archived"

    if c.score < config.limits.min_comment_score:
        return f"comment score {c.score} below minimum"

    age_hours = (time.time() - c.created_utc) / 3600 if c.created_utc else 0
    if config.limits.max_comment_age_hours and age_hours > config.limits.max_comment_age_hours:
        return f"comment too old ({age_hours:.1f}h)"

    if db.has_replied(c.id) or db.is_queued(c.id):
        return "already handled"

    return None


class Engine:
    def __init__(self, config: Config, db: Database, reddit: RedditClient | None):
        self.config = config
        self.db = db
        self.reddit = reddit
        if reddit is not None:
            # Record who we are so we never reply to ourselves.
            config.raw["_bot_username"] = reddit.username

    # -- scanning -----------------------------------------------------------
    def scan(self, per_sub_limit: int = 100) -> ScanReport:
        report = ScanReport()
        if self.reddit is None:
            raise RuntimeError("Reddit client not configured; cannot scan.")

        for sub in self.config.enabled_subreddits():
            for c in self.reddit.recent_comments(sub.name, limit=per_sub_limit):
                report.scanned += 1
                if self.db.has_seen(c.id):
                    continue
                self.db.mark_seen(c.id)

                reason = should_skip(self.config, self.db, c)
                if reason:
                    report.skipped += 1
                    log.debug("skip %s: %s", c.id, reason)
                    continue

                match = first_match(self.config, c.subreddit, c.body)
                if not match:
                    continue
                report.matched += 1

                # Rotation index = how many times this rule has fired so far,
                # so response variants cycle.
                rotation = self.db.count_replies_since(0)  # cheap monotonic-ish counter
                body = build_reply(self.config, match.rule, c.subreddit, rotation)

                if self.config.review_required:
                    if self.db.enqueue(
                        comment_id=c.id,
                        parent_id=c.parent_id,
                        subreddit=c.subreddit,
                        author=c.author,
                        rule_id=match.rule.id,
                        comment_body=c.body,
                        comment_link=c.permalink,
                        proposed_body=body,
                    ):
                        report.enqueued += 1
                        log.info("queued reply for %s (rule=%s)", c.id, match.rule.id)
                else:
                    if self._post(c, match.rule.id, body):
                        report.posted += 1

        return report

    # -- posting ------------------------------------------------------------
    def _post(self, c: CommentView, rule_id: str, body: str) -> bool:
        """Post (or dry-run) a reply to a live comment view. Returns True if it counted."""
        gate = allow_post(self.config, self.db, c.subreddit, c.author)
        if not gate.allowed:
            log.info("holding reply to %s: %s", c.id, gate.reason)
            return False

        if self.config.dry_run:
            self.db.record_reply(
                comment_id=c.id,
                parent_id=c.parent_id,
                subreddit=c.subreddit,
                author=c.author,
                rule_id=rule_id,
                body=body,
                status="dry_run",
                permalink=c.permalink,
            )
            log.info("[DRY RUN] would reply to %s in r/%s", c.id, c.subreddit)
            return True

        try:
            permalink = self.reddit.reply(c.fullname, body)
            self.db.record_reply(
                comment_id=c.id,
                parent_id=c.parent_id,
                subreddit=c.subreddit,
                author=c.author,
                rule_id=rule_id,
                body=body,
                status="posted",
                permalink=permalink,
            )
            log.info("replied to %s -> %s", c.id, permalink)
            return True
        except Exception as exc:  # pragma: no cover - network failure path
            self.db.record_reply(
                comment_id=c.id,
                parent_id=c.parent_id,
                subreddit=c.subreddit,
                author=c.author,
                rule_id=rule_id,
                body=body,
                status="failed",
                error=str(exc),
            )
            log.error("failed to reply to %s: %s", c.id, exc)
            return False

    def post_queue_item(self, item_id: int) -> tuple[bool, str]:
        """Post one approved queue item by id. Returns (ok, message)."""
        row = self.db.queue_item(item_id)
        if row is None:
            return False, "queue item not found"
        if row["status"] not in ("approved", "pending"):
            return False, f"item is '{row['status']}', not postable"
        if self.db.has_replied(row["comment_id"]):
            self.db.set_queue_status(item_id, "posted")
            return False, "already replied to this comment"

        gate = allow_post(self.config, self.db, row["subreddit"], row["author"])
        if not gate.allowed:
            return False, f"held by rate limit: {gate.reason}"

        if self.config.dry_run:
            self.db.record_reply(
                comment_id=row["comment_id"],
                parent_id=row["parent_id"],
                subreddit=row["subreddit"],
                author=row["author"],
                rule_id=row["rule_id"],
                body=row["proposed_body"],
                status="dry_run",
                permalink=row["comment_link"],
            )
            self.db.set_queue_status(item_id, "posted")
            return True, "dry-run: recorded but not posted"

        if self.reddit is None:
            return False, "Reddit client not configured"

        try:
            comment_fullname = f"t1_{row['comment_id']}"
            permalink = self.reddit.reply(comment_fullname, row["proposed_body"])
            self.db.record_reply(
                comment_id=row["comment_id"],
                parent_id=row["parent_id"],
                subreddit=row["subreddit"],
                author=row["author"],
                rule_id=row["rule_id"],
                body=row["proposed_body"],
                status="posted",
                permalink=permalink,
            )
            self.db.set_queue_status(item_id, "posted")
            return True, permalink
        except Exception as exc:  # pragma: no cover - network failure path
            self.db.set_queue_status(item_id, "failed", error=str(exc))
            return False, str(exc)

    def post_approved(self, limit: int = 50) -> int:
        posted = 0
        for row in self.db.approved_pending_post(limit=limit):
            ok, _ = self.post_queue_item(row["id"])
            if ok:
                posted += 1
        return posted

    def run_once(self, per_sub_limit: int = 100) -> ScanReport:
        report = self.scan(per_sub_limit=per_sub_limit)
        report.posted += self.post_approved()
        return report
