"""/live RFI kind-slot (pm_web display) — bundled with the unbooked-chip relabel (2026-09-14).

The game card had 3 fixed kinds (ML/TOT/SPR); a KXMLBRFI position fell to kind 'kxmlbrfi', absent from KINDS,
so a first RFI fill would UNDER-RENDER. This adds 'first_inning_run' as a 4th kind (label RFI), MLB-only,
rendered CONDITIONALLY so non-MLB cards are unaffected. These pin the mapping + the no-regression on the others.
"""
from trading_corp.prediction_markets.web import live_view as LV
from trading_corp.prediction_markets import market_describe as MD


def test_kind_maps_rfi():
    assert LV._kind("KXMLBRFI-26SEP162140MIAAZ") == "first_inning_run"
    assert "first_inning_run" in LV.KINDS
    assert LV.KIND_LABEL["first_inning_run"] == "RFI"


def test_kind_unchanged_for_the_other_families():
    assert LV._kind("KXMLBGAME-26SEP141945SFSTL-SF") == "moneyline"
    assert LV._kind("KXMLBTOTAL-26SEP141945SFSTL-7") == "total"
    assert LV._kind("KXMLBSPREAD-26SEP141945SFSTL-STL3") == "spread"
    assert LV._kind("KXNFLGAME-26SEP14DENKC-KC") == "moneyline"
    assert LV._kind("KXNFL1HSPREAD-26SEP14DENKC-KC8") == "spread"   # 1H spread still reads as spread (that's fine here)


def test_short_label_rfi_carries_side():
    assert LV._short_label("KXMLBRFI-26SEP162140MIAAZ", "first_inning_run", "yes") == "Run 1st"
    assert LV._short_label("KXMLBRFI-26SEP162140MIAAZ", "first_inning_run", "no") == "No Run 1st"
    assert LV._short_label("KXMLBRFI-26SEP162140MIAAZ", "first_inning_run", None) == "1st-inn run"


def test_describe_market_rfi_no_crash():
    d = MD.describe_market("KXMLBRFI-26SEP162140MIAAZ", "yes")
    assert isinstance(d, str) and d          # honest fallback -> never raises, always a string


def test_kinds_grid_keys_include_rfi():
    # slots_by_kind is built as {KIND_LABEL[k]: ... for k in KINDS}; the template renders .RFI conditionally.
    labels = [LV.KIND_LABEL[k] for k in LV.KINDS]
    assert labels[:3] == ["ML", "TOT", "SPR"] and "RFI" in labels
