"""Thin wrapper around PRAW (the official-API Python client).

Isolating PRAW here keeps the rest of the codebase testable without network
access, and gives us one place that knows how to authenticate, stream comments,
and post replies.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator

from .config import Secrets

log = logging.getLogger("bot.reddit")


@dataclass
class CommentView:
    """A normalized, network-free view of a Reddit comment for the engine."""

    id: str
    fullname: str          # e.g. "t1_abc123"
    body: str
    author: str | None
    subreddit: str
    permalink: str
    created_utc: float
    score: int
    is_locked: bool
    is_archived: bool
    parent_id: str


class RedditClient:
    def __init__(self, secrets: Secrets, *, check_for_async: bool = False):
        if not secrets.is_complete:
            raise RuntimeError(
                "Reddit credentials are incomplete. Set REDDIT_CLIENT_ID, "
                "REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD and "
                "REDDIT_USER_AGENT (see .env.example)."
            )
        # Import lazily so unit tests that never touch the network don't need praw.
        import praw

        self._reddit = praw.Reddit(
            client_id=secrets.client_id,
            client_secret=secrets.client_secret,
            username=secrets.username,
            password=secrets.password,
            user_agent=secrets.user_agent,
            check_for_async=check_for_async,
        )
        # PRAW honors Reddit's rate-limit headers automatically. Ask it to sleep
        # rather than raise when we bump the API limit, up to a sane ceiling.
        self._reddit.config.ratelimit_seconds = 600
        self.username = secrets.username

    def me(self) -> str:
        return str(self._reddit.user.me())

    @staticmethod
    def _to_view(comment) -> CommentView:
        sub = str(comment.subreddit)
        author = str(comment.author) if comment.author else None
        submission = comment.submission
        return CommentView(
            id=comment.id,
            fullname=comment.fullname,
            body=comment.body or "",
            author=author,
            subreddit=sub,
            permalink=f"https://www.reddit.com{comment.permalink}",
            created_utc=float(comment.created_utc or 0),
            score=int(getattr(comment, "score", 0) or 0),
            is_locked=bool(getattr(comment, "locked", False)),
            is_archived=bool(getattr(submission, "archived", False)),
            parent_id=str(comment.parent_id),
        )

    def recent_comments(self, subreddit: str, limit: int = 100) -> Iterator[CommentView]:
        """Yield the most recent comments in a subreddit (newest first)."""
        for comment in self._reddit.subreddit(subreddit).comments(limit=limit):
            try:
                yield self._to_view(comment)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("Skipping a comment that failed to load: %s", exc)

    def reply(self, comment_fullname: str, body: str) -> str:
        """Post a reply to a comment by fullname; return the new comment permalink."""
        comment = self._reddit.comment(id=comment_fullname.split("_")[-1])
        new_comment = comment.reply(body)
        return f"https://www.reddit.com{new_comment.permalink}"
