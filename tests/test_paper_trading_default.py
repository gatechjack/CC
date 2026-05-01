"""Verify the paper-default + --live confirmation safety flow."""
from __future__ import annotations

import io
import sys

import pytest

from trading_corp.main import confirm_live, parse_args


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
