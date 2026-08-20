"""Flask dashboard.

A small, password-protected web UI to:
  * see the bot's status (dry-run / live, review mode, today's counts),
  * review pending candidate replies and approve/reject them,
  * trigger a scan on demand,
  * browse recent activity.

Auth is a single operator login (HTTP form + session cookie), configured via
DASHBOARD_USERNAME / DASHBOARD_PASSWORD. This is meant to run privately (behind
your own network / a tunnel), not exposed to the open internet.
"""
from __future__ import annotations

import hmac
import os
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .config import ConfigError, load_config, load_secrets
from .db import Database
from .engine import Engine
from .reddit_client import RedditClient


def _check_login(username: str, password: str) -> bool:
    exp_user = os.environ.get("DASHBOARD_USERNAME", "admin")
    exp_pass = os.environ.get("DASHBOARD_PASSWORD", "")
    if not exp_pass:
        return False
    return hmac.compare_digest(username, exp_user) and hmac.compare_digest(password, exp_pass)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def _make_reddit_or_none() -> RedditClient | None:
    secrets = load_secrets()
    if not secrets.is_complete:
        return None
    try:
        return RedditClient(secrets)
    except Exception:
        return None


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-insecure-change-me")

    db = Database()

    def get_engine(need_reddit: bool = False) -> Engine:
        config = load_config()
        reddit = _make_reddit_or_none() if need_reddit else None
        return Engine(config, db, reddit)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            if _check_login(request.form.get("username", ""), request.form.get("password", "")):
                session["logged_in"] = True
                dest = request.args.get("next") or url_for("index")
                return redirect(dest)
            flash("Invalid credentials", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def index():
        try:
            config = load_config()
        except ConfigError as exc:
            return render_template("error.html", message=str(exc)), 500
        stats = db.stats()
        recent = db.recent_replies(limit=25)
        return render_template(
            "index.html",
            config=config,
            stats=stats,
            recent=recent,
            reddit_ready=load_secrets().is_complete,
        )

    @app.route("/review")
    @login_required
    def review():
        config = load_config()
        pending = db.queue_items(status="pending", limit=200)
        return render_template("review.html", config=config, pending=pending)

    @app.route("/review/<int:item_id>/<action>", methods=["POST"])
    @login_required
    def review_decide(item_id: int, action: str):
        if action not in ("approve", "reject"):
            abort(400)
        item = db.queue_item(item_id)
        if item is None:
            abort(404)
        if action == "reject":
            db.set_queue_status(item_id, "rejected")
            flash(f"Rejected candidate for comment {item['comment_id']}.", "ok")
            return redirect(url_for("review"))

        # approve -> mark approved, then try to post immediately
        db.set_queue_status(item_id, "approved")
        engine = get_engine(need_reddit=True)
        ok, msg = engine.post_queue_item(item_id)
        if ok:
            flash(f"Approved and posted: {msg}", "ok")
        else:
            flash(f"Approved but not posted yet ({msg}). It will post on the next cycle.", "warn")
        return redirect(url_for("review"))

    @app.route("/scan", methods=["POST"])
    @login_required
    def scan_now():
        engine = get_engine(need_reddit=True)
        if engine.reddit is None:
            flash("Reddit credentials not configured — cannot scan.", "error")
            return redirect(url_for("index"))
        try:
            report = engine.run_once()
            flash(
                f"Scan complete: scanned={report.scanned} matched={report.matched} "
                f"enqueued={report.enqueued} posted={report.posted} skipped={report.skipped}",
                "ok",
            )
        except Exception as exc:
            flash(f"Scan failed: {exc}", "error")
        return redirect(url_for("index"))

    @app.route("/activity")
    @login_required
    def activity():
        recent = db.recent_replies(limit=200)
        return render_template("activity.html", recent=recent)

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
