"""Reddit Helper Bot — a single-account, clearly-labeled Reddit assistant.

This package deliberately enforces a set of non-negotiable guardrails:

* It only ever acts in an explicit allowlist of subreddits.
* Every reply carries a bot-disclosure footer.
* It never replies to the same comment twice, and never to itself.

See the README for the full picture.
"""

__version__ = "1.0.0"
