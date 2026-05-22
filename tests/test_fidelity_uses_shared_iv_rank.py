"""Regression: `fidelity_options._calc_iv_rank` must resolve to the shared util.

The Fidelity duplicate at `fidelity_options.py:139-166` was deleted in
`a6885a5` and replaced with `from trading_corp.utils.iv import calc_iv_rank
as _calc_iv_rank`.  Byte-equivalence was proven by inspection + indirect
coverage; this test makes the binding explicit so a future rename or
import-path change can't silently un-share the math.
"""
from __future__ import annotations


def test_fidelity_calc_iv_rank_is_shared_util():
    from trading_corp.agents.divisions.fidelity_options import _calc_iv_rank
    from trading_corp.utils.iv import calc_iv_rank

    assert _calc_iv_rank is calc_iv_rank
