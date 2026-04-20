"""Day 7 tests: SMTP emailer behaves correctly (no network calls)."""

import os
from unittest.mock import MagicMock

import pytest

from classcheck.emailer import EmailConfigError, _load_config, send_csv_report


# --- Config loading ---

def test_load_config_requires_essential_env(monkeypatch):
    monkeypatch.delenv("CLASSCHECK_SMTP_HOST", raising=False)
    monkeypatch.delenv("CLASSCHECK_SMTP_USER", raising=False)
    monkeypatch.delenv("CLASSCHECK_SMTP_PASSWORD", raising=False)
    with pytest.raises(EmailConfigError, match="Missing env vars"):
        _load_config()


def test_load_config_defaults_port_and_from(monkeypatch):
    monkeypatch.setenv("CLASSCHECK_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("CLASSCHECK_SMTP_USER", "me@example.com")
    monkeypatch.setenv("CLASSCHECK_SMTP_PASSWORD", "pass")
    monkeypatch.delenv("CLASSCHECK_SMTP_PORT", raising=False)
    monkeypatch.delenv("CLASSCHECK_SMTP_FROM", raising=False)
    cfg = _load_config()
    assert cfg["port"] == 587
    assert cfg["from_addr"] == "me@example.com"
    assert cfg["use_tls"] is True


def test_load_config_invalid_port(monkeypatch):
    monkeypatch.setenv("CLASSCHECK_SMTP_HOST", "h")
    monkeypatch.setenv("CLASSCHECK_SMTP_USER", "u")
    monkeypatch.setenv("CLASSCHECK_SMTP_PASSWORD", "p")
    monkeypatch.setenv("CLASSCHECK_SMTP_PORT", "nope")
    with pytest.raises(EmailConfigError, match="not an integer"):
        _load_config()


# --- send_csv_report ---

def test_send_requires_recipients():
    with pytest.raises(ValueError, match="at least one recipient"):
        send_csv_report(
            to=[],
            subject="x",
            body="y",
            csv_text="a,b\n",
            csv_filename="x.csv",
            config={"host": "h", "port": 587, "user": "u", "password": "p",
                    "from_addr": "u", "use_tls": True},
            smtp_client=MagicMock(),
        )


def test_send_uses_injected_smtp_client():
    """With smtp_client injected, no real SMTP connection is made."""
    mock_smtp = MagicMock()
    send_csv_report(
        to=["teacher@school.edu"],
        subject="Test",
        body="Body",
        csv_text="Name,Status\nAlice,PRESENT\n",
        csv_filename="roll.csv",
        cc=["admin@school.edu"],
        config={"host": "h", "port": 587, "user": "u", "password": "p",
                "from_addr": "u", "use_tls": True},
        smtp_client=mock_smtp,
    )
    # send_message was called exactly once
    mock_smtp.send_message.assert_called_once()
    # Check the message was addressed correctly
    args, kwargs = mock_smtp.send_message.call_args
    to_addrs = kwargs.get("to_addrs") or (args[1] if len(args) > 1 else None)
    assert "teacher@school.edu" in to_addrs
    assert "admin@school.edu" in to_addrs


def test_send_attaches_csv_with_correct_filename():
    mock_smtp = MagicMock()
    send_csv_report(
        to=["a@b.c"],
        subject="S",
        body="B",
        csv_text="col1,col2\n1,2\n",
        csv_filename="myroll.csv",
        config={"host": "h", "port": 587, "user": "u", "password": "p",
                "from_addr": "u", "use_tls": True},
        smtp_client=mock_smtp,
    )
    msg = mock_smtp.send_message.call_args[0][0]
    # The attached CSV should appear as a part
    parts = list(msg.iter_attachments())
    assert len(parts) == 1
    att = parts[0]
    assert att.get_filename() == "myroll.csv"
    assert att.get_content_type() == "text/csv"
