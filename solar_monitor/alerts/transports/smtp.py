"""SMTP / email transport.

User configures their own SMTP credentials in config.yaml, Gmail app
password, an ISP relay, AWS SES, etc. We don't ship a default broker
because email-from-an-appliance is a deliverability minefield (SPF /
DKIM / DMARC against arbitrary domains). The price of asking the user
for credentials is paid back by mail that actually reaches the inbox.

Plain text body for portability; subject carries the severity + rule
name so triage works from the notification preview.
"""
from __future__ import annotations

import logging
import time
from email.message import EmailMessage

import aiosmtplib

from ..base import AlertEvent, NotificationTransport
from ..registry import register_notification_transport

log = logging.getLogger(__name__)


_SEVERITY_TAG = {"warn": "WARN", "alarm": "ALARM"}


class SmtpTransport(NotificationTransport):
    def __init__(
        self,
        id: str,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        from_addr: str,
        to_addrs: list[str],
        use_starttls: bool = True,
        use_ssl: bool = False,
    ) -> None:
        self.id = id
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.to_addrs = to_addrs
        self.use_starttls = use_starttls
        self.use_ssl = use_ssl

    async def send(self, event: AlertEvent) -> None:
        from ..base import (
            humanise_metric, humanise_op, fmt_value,
        )
        tag = _SEVERITY_TAG.get(event.severity, "WARN")
        metric_label = humanise_metric(event.metric)
        cur          = fmt_value(event.metric, event.value)
        thr          = fmt_value(event.metric, event.threshold)
        op_word      = humanise_op(event.op)
        # Subject leads with the metric + current value so a phone
        # lock-screen preview answers "what?" + "how bad?" without
        # opening the message.  Rule name in parens to identify it.
        subject = f"WattPost {tag.lower()}: {metric_label.lower()} {cur} ({event.name})"
        body = (
            f"Your WattPost appliance fired the \"{event.name}\" rule.\n"
            f"\n"
            f"  {metric_label} is now {cur}\n"
            f"  Rule threshold: {op_word} {thr}\n"
            f"  Severity: {event.severity.upper()}\n"
            f"  Fired at: {time.strftime('%Y-%m-%d %H:%M:%S %z', time.localtime(event.ts))}\n"
            f"\n"
            f"This is an automated notification from your local appliance.\n"
            f"Open the dashboard for live data and to manage alert rules.\n"
        )

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)
        msg.set_content(body)

        try:
            # aiosmtplib opens, authenticates, sends, and closes per call,
            # simpler than holding a connection open between alert fires
            # (most setups send <10 mails/day, no benefit to pooling).
            await aiosmtplib.send(
                msg,
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                start_tls=self.use_starttls and not self.use_ssl,
                use_tls=self.use_ssl,
                timeout=20,
            )
        except Exception as e:
            log.warning("[%s] smtp send failed: %s", self.id, e)


@register_notification_transport("smtp")
def _factory(cfg: dict) -> SmtpTransport:
    to = cfg.get("to_addrs") or cfg.get("to")
    if isinstance(to, str):
        to = [to]
    if not to:
        raise ValueError(f"smtp transport {cfg.get('id')!r}: missing to_addrs")
    return SmtpTransport(
        id=cfg["id"],
        host=cfg["host"],
        port=int(cfg.get("port", 587)),
        username=cfg.get("username"),
        password=cfg.get("password"),
        from_addr=cfg.get("from_addr") or cfg.get("from")
                  or cfg.get("username")
                  or "WattPost <noreply@wattpost.local>",
        to_addrs=list(to),
        use_starttls=bool(cfg.get("use_starttls", True)),
        use_ssl=bool(cfg.get("use_ssl", False)),
    )
