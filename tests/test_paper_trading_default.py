"""Verify the paper-default + --live confirmation safety flow."""
from __future__ import annotations

import io
import sys

import pytest

from trading_corp.main import (
    confirm_live,
    parse_args,
    live_authorized_noninteractive,
    resolve_live_decision,
    LIVE_AUTH_ENV,
)


def test_no_args_means_paper():
    args = parse_args([])
    assert args.live is False
    assert args.demo is False


def test_live_flag_present():
    args = parse_args(["--live"])
    assert args.live is True


def test_confirm_live_requires_exact_LIVE():
    assert confirm_live(input_fn=lambda: "LIVE") is True
    assert confirm_live(input_fn=lambda: "live") is False
    assert confirm_live(input_fn=lambda: "yes") is False
    assert confirm_live(input_fn=lambda: "") is False
    assert confirm_live(input_fn=lambda: " LIVE ") is True  # stripped


def test_confirm_live_handles_eof():
    def boom():
        raise EOFError
    assert confirm_live(input_fn=boom) is False


def test_paper_default_in_disclaimer():
    # The disclaimer string must mention PAPER as the default mode.
    from trading_corp.main import DISCLAIMER
    assert "PAPER" in DISCLAIMER
    assert "DEFAULTS to PAPER" in DISCLAIMER


# ── Item 4: non-interactive durable LIVE authorization ───────────────────


def test_noninteractive_auth_set_authorizes_live():
    assert live_authorized_noninteractive({LIVE_AUTH_ENV: "LIVE"}) is True
    assert live_authorized_noninteractive({LIVE_AUTH_ENV: " LIVE "}) is True  # stripped


def test_noninteractive_auth_absent_is_not_live():
    assert live_authorized_noninteractive({}) is False
    assert live_authorized_noninteractive({LIVE_AUTH_ENV: ""}) is False


def test_noninteractive_auth_revoked_is_not_live():
    # Revoked = flipped to anything other than the exact token.
    assert live_authorized_noninteractive({LIVE_AUTH_ENV: "PAPER"}) is False
    assert live_authorized_noninteractive({LIVE_AUTH_ENV: "no"}) is False
    assert live_authorized_noninteractive({LIVE_AUTH_ENV: "live"}) is False  # exact-case


def test_noninteractive_auth_is_durable_across_restart():
    """Durable (operator decision 2026-06-13): the SAME env re-authorizes live
    on every call — modeling a crash / Restart=on-failure re-launch that
    re-reads the still-set env. No consumption."""
    env = {LIVE_AUTH_ENV: "LIVE"}
    assert live_authorized_noninteractive(env) is True
    assert live_authorized_noninteractive(env) is True  # 2nd start: still live
    assert env == {LIVE_AUTH_ENV: "LIVE"}  # unchanged — nothing consumed


# ── Item 4: the startup live/paper/abort decision ────────────────────────


def test_decision_no_live_is_paper():
    assert resolve_live_decision(want_live=False, interactive=False) == "paper"
    assert resolve_live_decision(want_live=False, interactive=True) == "paper"


def test_decision_interactive_typed_live_is_live():
    assert resolve_live_decision(
        want_live=True, interactive=True, input_fn=lambda: "LIVE",
    ) == "live"


def test_decision_interactive_declined_aborts():
    assert resolve_live_decision(
        want_live=True, interactive=True, input_fn=lambda: "no",
    ) == "abort"


def test_decision_noninteractive_authorized_is_live():
    assert resolve_live_decision(
        want_live=True, interactive=False, env={LIVE_AUTH_ENV: "LIVE"},
    ) == "live"


def test_decision_noninteractive_unauthorized_downgrades_to_paper_not_abort():
    """Load-bearing safety property: non-interactive --live WITHOUT auth must
    DOWNGRADE to 'paper' (run paper) — NOT 'abort' (which maps to `return 2`
    and would crash-loop under systemd Restart=on-failure)."""
    assert resolve_live_decision(
        want_live=True, interactive=False, env={},
    ) == "paper"
    assert resolve_live_decision(
        want_live=True, interactive=False, env={LIVE_AUTH_ENV: "PAPER"},
    ) == "paper"


def test_decision_noninteractive_durable_crash_restart_stays_live():
    """Durable: a non-interactive restart with the SAME env returns 'live'
    again — a crash-restart resurrects live without re-arming."""
    env = {LIVE_AUTH_ENV: "LIVE"}
    assert resolve_live_decision(want_live=True, interactive=False, env=env) == "live"
    assert resolve_live_decision(want_live=True, interactive=False, env=env) == "live"
