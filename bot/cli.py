"""Command-line entry point.

    python -m bot.cli check       # verify config + Reddit auth
    python -m bot.cli scan        # one scan pass (enqueue or post per config)
    python -m bot.cli run         # continuous loop (scan + post approved)
    python -m bot.cli post-approved
    python -m bot.cli web         # start the dashboard
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from .config import ConfigError, load_config, load_secrets
from .db import Database
from .engine import Engine
from .reddit_client import RedditClient


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _build_engine(need_reddit: bool) -> Engine:
    config = load_config()
    db = Database()
    reddit = None
    if need_reddit:
        reddit = RedditClient(load_secrets())
    return Engine(config, db, reddit)


def cmd_check(_args) -> int:
    config = load_config()
    print(f"Config OK: {len(config.enabled_subreddits())} subreddit(s) enabled, "
          f"{sum(1 for r in config.rules if r.enabled)} rule(s) enabled.")
    print(f"dry_run={config.dry_run}  review_required={config.review_required}")
    secrets = load_secrets()
    if not secrets.is_complete:
        print("Reddit credentials incomplete — set them in .env before running scan/run.")
        return 1
    reddit = RedditClient(secrets)
    who = reddit.me()
    print(f"Authenticated with Reddit as u/{who}.")
    if who.lower() != secrets.username.lower():
        print("WARNING: authenticated user differs from REDDIT_USERNAME.")
    return 0


def cmd_scan(_args) -> int:
    engine = _build_engine(need_reddit=True)
    report = engine.run_once()
    print(
        f"scanned={report.scanned} matched={report.matched} "
        f"enqueued={report.enqueued} posted={report.posted} skipped={report.skipped}"
    )
    return 0


def cmd_run(args) -> int:
    engine = _build_engine(need_reddit=True)
    interval = max(30, args.interval)
    logging.getLogger("bot").info("starting loop, interval=%ss", interval)
    while True:
        try:
            report = engine.run_once()
            logging.getLogger("bot").info(
                "cycle: scanned=%s matched=%s enqueued=%s posted=%s skipped=%s",
                report.scanned, report.matched, report.enqueued, report.posted, report.skipped,
            )
        except Exception as exc:  # keep the loop alive across transient errors
            logging.getLogger("bot").exception("cycle failed: %s", exc)
        time.sleep(interval)


def cmd_post_approved(_args) -> int:
    engine = _build_engine(need_reddit=True)
    n = engine.post_approved()
    print(f"posted {n} approved item(s)")
    return 0


def cmd_web(args) -> int:
    from .web import create_app

    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bot", description="Reddit Helper Bot")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="validate config and Reddit auth")
    sub.add_parser("scan", help="run one scan/post pass")

    p_run = sub.add_parser("run", help="run continuously")
    p_run.add_argument("--interval", type=int, default=180, help="seconds between cycles")

    sub.add_parser("post-approved", help="post approved queue items")

    p_web = sub.add_parser("web", help="start the dashboard")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=5000)
    p_web.add_argument("--debug", action="store_true")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    handlers = {
        "check": cmd_check,
        "scan": cmd_scan,
        "run": cmd_run,
        "post-approved": cmd_post_approved,
        "web": cmd_web,
    }
    try:
        return handlers[args.command](args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
