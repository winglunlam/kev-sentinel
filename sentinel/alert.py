"""Alert on the transition into failure, not on every failing run.

A checker that pages you every hour while an endpoint is down is a checker people
mute. The rule is: alert when the previous run passed (or this is the first run)
and this one failed, never more often than ``alert_cooldown_s``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from .config import Config, EndpointConfig
from .core.store import Store

if TYPE_CHECKING:
    from .check import EndpointResult


@dataclass(frozen=True)
class AlertOutcome:
    sent: bool
    skipped: str | None = None
    error: str | None = None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _cooldown_over(config: Config, endpoint: EndpointConfig, store: Store) -> bool:
    last = store.last_alert_at(endpoint.name)
    last_dt = _parse_ts(last)
    if last_dt is None:
        return False
    elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
    return elapsed < config.alert_cooldown_s


def webhook_payload(result: "EndpointResult") -> dict:
    reasons = result.reasons[:8]
    lines = [f"sentinel: {result.endpoint} failed"]
    lines.extend(f"- {reason}" for reason in reasons)
    text = "\n".join(lines)
    return {
        "source": "kev-sentinel",
        "text": text,
        "endpoint": result.endpoint,
        "ok": result.ok,
        "ts": result.ts,
        "reasons": reasons,
        "url": None,
    }


def maybe_alert(
    config: Config,
    endpoint: EndpointConfig,
    result: "EndpointResult",
    *,
    previous_ok: bool | None,
    store: Store,
    transport: httpx.BaseTransport | None = None,
    timeout_s: float = 10.0,
) -> AlertOutcome:
    if result.ok:
        return AlertOutcome(False, "run passed")
    if previous_ok:
        return AlertOutcome(False, "already failing in the previous run")
    if config.webhook_url is None and not _env_webhook():
        return AlertOutcome(False, "no webhook configured")
    if _cooldown_over(config, endpoint, store):
        return AlertOutcome(False, "alert cooldown active")

    url = config.webhook_url or _env_webhook()
    payload = webhook_payload(result)
    try:
        with httpx.Client(timeout=timeout_s, transport=transport) as client:
            response = client.post(url, json=payload)
        if response.status_code >= 400:
            return AlertOutcome(False, error=f"webhook returned HTTP {response.status_code}")
    except (httpx.HTTPError, ValueError) as exc:
        return AlertOutcome(False, error=f"webhook failed: {exc}")
    return AlertOutcome(True)


def _env_webhook() -> str | None:
    import os

    return os.environ.get("SENTINEL_WEBHOOK_URL") or None