# Reddit Helper Bot

A **single-account, clearly-labeled** Reddit assistant with a private web
dashboard. It watches an **allowlist** of subreddits for comments matching your
keyword rules and posts a helpful, bot-disclosed reply — with rate limits, a
per-user cooldown, deduplication, and an optional human-approval queue.

This project is built to operate **within Reddit's rules and the rules of the
subreddits you target**. It runs as one authenticated account via the official
Reddit API, identifies itself as a bot on every reply, and is capped so it
behaves politely. It is deliberately **not** a mass-reply / multi-account /
ban-evasion tool.

> **Your responsibility:** Only add subreddits that permit bots. Many subreddits
> forbid automated replies — check each subreddit's rules/wiki or ask the mods
> first. Follow the [Reddit API Terms](https://www.redditinc.com/policies/data-api-terms)
> and [Reddit Rules](https://www.redditinc.com/policies/reddit-rules). Bots that
> spam or post without disclosure get accounts and apps banned — correctly.

---

## What it does

- **Scans** recent comments in the subreddits you allow.
- **Matches** them against keyword rules (word-boundary, case-insensitive, with
  `any_of` / `all_of` / `none_of` / regex).
- **Composes** a reply from your response templates and **always appends a bot
  disclosure footer** (the code refuses to post without one).
- **Gates** every post through rate limits: minimum spacing between posts,
  per-subreddit and global daily caps, per-user cooldown, comment age/score
  filters, and optional quiet hours.
- **Dedupes** so it never replies to the same comment twice, and never to
  itself or to other bots.
- Offers a **review queue** in the dashboard so a human approves each reply
  before it posts (default on). Turn it off for fully automatic replies once you
  trust your rules.
- Ships a **dry-run mode** (default on) that logs what it *would* post without
  posting anything.

## Guardrails you can't switch off

1. **Allowlist only.** The bot acts solely in the subreddits you list under
   `subreddits:`. Anything else is ignored.
2. **Mandatory disclosure.** Every reply ends with your `disclosure` footer.
   An empty disclosure makes the bot refuse to start / refuse to build a reply.
3. **No double replies / no self-replies.** Enforced via the local database.

---

## Quick start (local)

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure secrets
cp .env.example .env
#   -> edit .env with your Reddit app credentials + dashboard password

# 3. Configure behavior
cp config.example.yaml config.yaml
#   -> edit subreddits (allowlist) and rules (keywords -> responses)

# 4. Verify everything is wired up
python -m bot.cli check

# 5. Launch the dashboard
python -m bot.cli web
#   -> open http://127.0.0.1:5000, log in, click "Scan now"
```

While `dry_run: true` (the default), nothing is ever posted — the dashboard and
activity log show exactly what *would* happen. When you're satisfied, set
`dry_run: false` in `config.yaml`.

### Getting Reddit API credentials

1. Log in as the **dedicated bot account** and go to
   <https://www.reddit.com/prefs/apps>.
2. **Create App** → type **script**. Redirect URI can be `http://localhost:8080`.
3. Copy the **client id** (under the app name) and the **secret** into `.env`.
4. Set a descriptive `REDDIT_USER_AGENT`, e.g.
   `web:reddit-helper-bot:1.0 (by /u/your_username)`. Reddit rejects generic
   user-agents.

---

## Running it

There are three processes, all driven by the same config and database:

| Command | Purpose |
| --- | --- |
| `python -m bot.cli web` | The dashboard (review, activity, controls). |
| `python -m bot.cli run --interval 180` | Background loop: scan + post approved. |
| `python -m bot.cli scan` | A single scan/post pass (good for cron / Actions). |
| `python -m bot.cli check` | Validate config + Reddit auth. |
| `python -m bot.cli post-approved` | Post any approved-but-unposted items. |

### Deploy with Docker (recommended for a private host)

```bash
cp .env.example .env            # fill in
cp config.example.yaml config.yaml   # fill in
docker compose up -d --build
```

This starts the **dashboard** (bound to `127.0.0.1:5000`) and the **scanner**
loop. The dashboard is intentionally bound to localhost — put it behind a VPN,
SSH tunnel, or an authenticating reverse proxy; don't expose it to the open
internet.

### "Serverless" scheduled mode (GitHub Actions)

`.github/workflows/scan.yml` runs a scan on a cron using repository secrets, so
you can host it entirely on GitHub with no server. Because there's no dashboard
in this mode, use it only with `review_required: false`. Add the `REDDIT_*`
secrets under **Settings → Secrets and variables → Actions**. State is persisted
best-effort via the Actions cache; for anything beyond experimentation, prefer
the Docker deployment.

---

## Configuration reference

Behavior lives in `config.yaml`; secrets live in `.env`. See the heavily
commented `config.example.yaml` for the full schema. The essentials:

```yaml
dry_run: true            # log-only until you flip this
review_required: true    # human approves each reply in the dashboard
disclosure: "I am a bot. ..."   # appended to every reply (required)

limits:
  min_seconds_between_replies: 120
  max_replies_per_subreddit_per_day: 10
  max_replies_total_per_day: 40
  per_user_cooldown_hours: 24
  max_comment_age_hours: 24
  min_comment_score: 1

subreddits:              # ALLOWLIST — the only subs the bot will touch
  - name: test
    enabled: true

rules:                   # keyword -> response
  - id: praw-question
    enabled: true
    match:
      any_of: ["how do I use praw", "praw not working"]
      none_of: ["not python"]
    responses:
      - "Helpful, on-topic reply text (disclosure is added automatically)."
```

### Response rotation

If a rule has multiple `responses`, the bot rotates through them so it doesn't
paste identical text repeatedly.

---

## Testing

```bash
pytest -q
```

The suite covers keyword matching (including word-boundary false-positive
avoidance), rate-limiting decisions, response rotation, and the mandatory
disclosure guarantee. CI runs it on every push (`.github/workflows/ci.yml`).

---

## Project layout

```
bot/
  config.py        # load + validate config and secrets
  db.py            # SQLite: replies, review queue, seen-comments
  matcher.py       # keyword / regex matching
  responder.py     # build reply + append disclosure
  ratelimit.py     # the single "may I post now?" gate
  reddit_client.py # PRAW wrapper (official API)
  engine.py        # scan -> decide -> enqueue/post orchestration
  web.py           # Flask dashboard
  cli.py           # command-line entry point
  templates/ static/
tests/
.github/workflows/ # CI + optional scheduled scan
Dockerfile  docker-compose.yml
```

## Notes on responsible use

- Keep `review_required: true` until you've read a batch of proposed replies and
  trust your rules. Bad keyword rules produce off-topic replies, which annoy
  people and get bots banned.
- Prefer replies that genuinely help (answer a question, point to a resource).
  If a reply wouldn't be welcome from a human, it won't be welcome from a bot.
- Honor opt-outs: if someone asks the bot to stop, add them to `skip.users`.
- One account, disclosed, rate-limited, allowlisted. That's the whole idea.
