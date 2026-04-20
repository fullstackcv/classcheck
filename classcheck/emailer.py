"""SMTP delivery for CSV reports.

Credentials come from environment variables (so they're never in the DB or
checked into git):

    CLASSCHECK_SMTP_HOST       e.g. "smtp.gmail.com"
    CLASSCHECK_SMTP_PORT       e.g. "587"
    CLASSCHECK_SMTP_USER       e.g. "you@example.com"
    CLASSCHECK_SMTP_PASSWORD   the password or app-specific token
    CLASSCHECK_SMTP_FROM       From: address (defaults to SMTP_USER)
    CLASSCHECK_SMTP_USE_TLS    "1" (default) or "0" for plaintext

Usage:

    from classcheck.emailer import send_csv_report
    send_csv_report(
        to=["teacher@school.edu"],
        subject="Daily roll — Math-10A",
        body="Attached.",
        csv_text=csv_string,
        csv_filename="roll_2026-04-17.csv",
    )
"""

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger("classcheck.emailer")


class EmailConfigError(RuntimeError):
    """Raised when SMTP env vars are missing or invalid."""


def _load_config() -> dict:
    host = os.environ.get("CLASSCHECK_SMTP_HOST")
    user = os.environ.get("CLASSCHECK_SMTP_USER")
    pwd = os.environ.get("CLASSCHECK_SMTP_PASSWORD")
    port_str = os.environ.get("CLASSCHECK_SMTP_PORT", "587")
    from_addr = os.environ.get("CLASSCHECK_SMTP_FROM", user)
    use_tls = os.environ.get("CLASSCHECK_SMTP_USE_TLS", "1") == "1"

    missing = [name for name, val in (
        ("CLASSCHECK_SMTP_HOST", host),
        ("CLASSCHECK_SMTP_USER", user),
        ("CLASSCHECK_SMTP_PASSWORD", pwd),
    ) if not val]
    if missing:
        raise EmailConfigError(
            f"Missing env vars: {', '.join(missing)}. "
            f"Set them before sending email."
        )
    try:
        port = int(port_str)
    except ValueError as e:
        raise EmailConfigError(f"CLASSCHECK_SMTP_PORT={port_str!r} is not an integer") from e

    return {
        "host": host, "port": port, "user": user, "password": pwd,
        "from_addr": from_addr, "use_tls": use_tls,
    }


def send_csv_report(
    to: list[str],
    subject: str,
    body: str,
    csv_text: str,
    csv_filename: str,
    cc: Optional[list[str]] = None,
    config: Optional[dict] = None,
    smtp_client: Optional[smtplib.SMTP] = None,
) -> None:
    """Send a CSV as an attachment via SMTP.

    Args:
        to: list of recipient email addresses.
        subject: email subject line.
        body: plain-text body.
        csv_text: the CSV content (from classcheck.reports.*).
        csv_filename: filename for the attachment (what the recipient sees).
        cc: optional CC list.
        config: optional dict overriding env vars — used by tests. Keys:
            host, port, user, password, from_addr, use_tls.
        smtp_client: optional pre-built SMTP client — used by tests to inject
            a mock; timeouts and TLS are not applied when provided.

    Raises:
        EmailConfigError if SMTP env vars are missing and `config` isn't provided.
        smtplib exceptions on network/auth failure.
    """
    if not to:
        raise ValueError("`to` must contain at least one recipient")

    cfg = config or _load_config()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content(body)
    msg.add_attachment(
        csv_text.encode("utf-8"),
        maintype="text",
        subtype="csv",
        filename=csv_filename,
    )

    all_recipients = list(to) + list(cc or [])

    if smtp_client is not None:
        # Test path: caller-provided client; just send.
        smtp_client.send_message(msg, to_addrs=all_recipients)
        logger.info("Sent %s → %s", csv_filename, all_recipients)
        return

    # Production path: SMTP with timeout + STARTTLS.
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as server:
        if cfg["use_tls"]:
            server.starttls()
        server.login(cfg["user"], cfg["password"])
        server.send_message(msg, to_addrs=all_recipients)
        logger.info("Sent %s → %s via %s:%d", csv_filename, all_recipients, cfg["host"], cfg["port"])
