"""SQLite persistence.

Tracks three things:
  * `replies`   — every reply we've posted (or would post, in dry-run), used for
                  dedup, rate limiting, per-user cooldowns, and the activity log.
  * `queue`     — candidate replies awaiting human review (review mode).
  * `seen`      — comment ids we've already evaluated, so we don't re-process.

Plain stdlib sqlite3, no ORM — the schema is tiny and this keeps deps minimal.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator

from .config import DEFAULT_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS replies (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id     TEXT NOT NULL,
    parent_id      TEXT,
    subreddit      TEXT NOT NULL,
    author         TEXT,
    rule_id        TEXT,
    body           TEXT NOT NULL,
    posted_at      REAL NOT NULL,
    status         TEXT NOT NULL,          -- posted | dry_run | failed
    permalink      TEXT,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_replies_comment ON replies(comment_id);
CREATE INDEX IF NOT EXISTS idx_replies_posted ON replies(posted_at);
CREATE INDEX IF NOT EXISTS idx_replies_sub ON replies(subreddit);
CREATE INDEX IF NOT EXISTS idx_replies_author ON replies(author);

CREATE TABLE IF NOT EXISTS queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id     TEXT NOT NULL UNIQUE,
    parent_id      TEXT,
    subreddit      TEXT NOT NULL,
    author         TEXT,
    rule_id        TEXT,
    comment_body   TEXT,
    comment_link   TEXT,
    proposed_body  TEXT NOT NULL,
    created_at     REAL NOT NULL,
    status         TEXT NOT NULL,          -- pending | approved | rejected | posted | failed
    decided_at     REAL,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);

CREATE TABLE IF NOT EXISTS seen (
    comment_id     TEXT PRIMARY KEY,
    seen_at        REAL NOT NULL
);
"""


class Database:
    def __init__(self, path: str | None = None):
        self.path = path or DEFAULT_DB_PATH
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # -- seen ---------------------------------------------------------------
    def mark_seen(self, comment_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen(comment_id, seen_at) VALUES (?, ?)",
                (comment_id, time.time()),
            )

    def has_seen(self, comment_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen WHERE comment_id = ?", (comment_id,)
            ).fetchone()
            return row is not None

    # -- replies ------------------------------------------------------------
    def has_replied(self, comment_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM replies WHERE comment_id = ? AND status IN ('posted','dry_run')",
                (comment_id,),
            ).fetchone()
            return row is not None

    def record_reply(
        self,
        *,
        comment_id: str,
        parent_id: str | None,
        subreddit: str,
        author: str | None,
        rule_id: str | None,
        body: str,
        status: str,
        permalink: str | None = None,
        error: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO replies
                   (comment_id, parent_id, subreddit, author, rule_id, body,
                    posted_at, status, permalink, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    comment_id,
                    parent_id,
                    subreddit,
                    author,
                    rule_id,
                    body,
                    time.time(),
                    status,
                    permalink,
                    error,
                ),
            )
            return int(cur.lastrowid)

    def count_replies_since(self, since_ts: float, subreddit: str | None = None) -> int:
        q = "SELECT COUNT(*) AS c FROM replies WHERE posted_at >= ? AND status IN ('posted','dry_run')"
        args: list = [since_ts]
        if subreddit:
            q += " AND subreddit = ?"
            args.append(subreddit)
        with self.connect() as conn:
            return int(conn.execute(q, args).fetchone()["c"])

    def last_reply_ts(self) -> float | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MAX(posted_at) AS t FROM replies WHERE status IN ('posted','dry_run')"
            ).fetchone()
            return row["t"] if row and row["t"] is not None else None

    def last_reply_ts_for_user(self, author: str) -> float | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MAX(posted_at) AS t FROM replies "
                "WHERE author = ? AND status IN ('posted','dry_run')",
                (author,),
            ).fetchone()
            return row["t"] if row and row["t"] is not None else None

    def recent_replies(self, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM replies ORDER BY posted_at DESC LIMIT ?", (limit,)
                ).fetchall()
            )

    # -- queue --------------------------------------------------------------
    def enqueue(
        self,
        *,
        comment_id: str,
        parent_id: str | None,
        subreddit: str,
        author: str | None,
        rule_id: str | None,
        comment_body: str | None,
        comment_link: str | None,
        proposed_body: str,
    ) -> bool:
        """Return True if a new row was inserted, False if it already existed."""
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO queue
                   (comment_id, parent_id, subreddit, author, rule_id,
                    comment_body, comment_link, proposed_body, created_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    comment_id,
                    parent_id,
                    subreddit,
                    author,
                    rule_id,
                    comment_body,
                    comment_link,
                    proposed_body,
                    time.time(),
                ),
            )
            return cur.rowcount > 0

    def is_queued(self, comment_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM queue WHERE comment_id = ?", (comment_id,)
            ).fetchone()
            return row is not None

    def queue_items(self, status: str = "pending", limit: int = 200) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM queue WHERE status = ? ORDER BY created_at ASC LIMIT ?",
                    (status, limit),
                ).fetchall()
            )

    def queue_item(self, item_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM queue WHERE id = ?", (item_id,)).fetchone()

    def set_queue_status(
        self, item_id: int, status: str, *, error: str | None = None
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE queue SET status = ?, decided_at = ?, error = ? WHERE id = ?",
                (status, time.time(), error, item_id),
            )

    def approved_pending_post(self, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM queue WHERE status = 'approved' ORDER BY decided_at ASC LIMIT ?",
                    (limit,),
                ).fetchall()
            )

    def stats(self) -> dict:
        now = time.time()
        day_ago = now - 86400
        with self.connect() as conn:
            posted_today = conn.execute(
                "SELECT COUNT(*) c FROM replies WHERE posted_at >= ? AND status = 'posted'",
                (day_ago,),
            ).fetchone()["c"]
            dry_today = conn.execute(
                "SELECT COUNT(*) c FROM replies WHERE posted_at >= ? AND status = 'dry_run'",
                (day_ago,),
            ).fetchone()["c"]
            pending = conn.execute(
                "SELECT COUNT(*) c FROM queue WHERE status = 'pending'"
            ).fetchone()["c"]
            total_posted = conn.execute(
                "SELECT COUNT(*) c FROM replies WHERE status = 'posted'"
            ).fetchone()["c"]
        return {
            "posted_today": posted_today,
            "dry_run_today": dry_today,
            "pending_review": pending,
            "total_posted": total_posted,
        }
