"""Optional webhook delivery.

Entirely opt-in: with no webhook environment variable set, nothing is sent and
nothing fails. Slack and Discord are detected by URL so there is no extra config
knob to get wrong.
"""

from __future__ import annotations

import logging
import os

import requests

from trend.models import Digest
from trend.render import render_summary_text

log = logging.getLogger(__name__)

SLACK_ENV = "SLACK_WEBHOOK_URL"
DISCORD_ENV = "DISCORD_WEBHOOK_URL"

#: Discord rejects messages over 2000 characters outright.
_DISCORD_LIMIT = 1900


def notify(digest: Digest, *, session: requests.Session | None = None) -> list[str]:
    """Post the digest summary to any configured webhook.

    Returns the names of the targets that accepted it. Failures are logged, not
    raised: the digest file is already on disk and a webhook outage should not
    fail the run.
    """
    session = session or requests.Session()
    text = render_summary_text(digest)
    delivered: list[str] = []

    slack_url = os.environ.get(SLACK_ENV, "").strip()
    if slack_url and _post(session, slack_url, {"text": text}, "slack"):
        delivered.append("slack")

    discord_url = os.environ.get(DISCORD_ENV, "").strip()
    if discord_url:
        content = text if len(text) <= _DISCORD_LIMIT else text[:_DISCORD_LIMIT] + "\n..."
        if _post(session, discord_url, {"content": content}, "discord"):
            delivered.append("discord")

    return delivered


def _post(session: requests.Session, url: str, payload: dict, label: str) -> bool:
    try:
        resp = session.post(url, json=payload, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("notify: %s delivery failed: %s", label, exc)
        return False
    log.info("notify: delivered to %s", label)
    return True
