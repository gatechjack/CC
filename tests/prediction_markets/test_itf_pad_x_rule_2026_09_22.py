"""ITF trailing-X pad rule (2026-09-22). Kalshi pads a two-letter surname to a 3-char code with a trailing X
(Ipek Oz -> OZX), which fails the ordered-subsequence corroboration in live_driver._audit_leg_independent yet is
correct. This adds a NARROW rule -- consulted ONLY after the subsequence AND alias checks fail -- that clears it as
'ok:code_pad'. It MUST stay narrower than the check it relaxes: ITF only, code exactly 3 chars ending in X, and the
outcome's LAST TOKEN folded must EQUAL code[:2] folded (equality, not subsequence). It must fail in the SAFE
direction (a false flag, never a false clear). These pin the rule, every anti-case, and the read-side clean-ness.
"""
from trading_corp.prediction_markets.live_driver import _audit_leg_independent as AUD
from trading_corp.prediction_markets import leg_audit as LA

OZX = "KXITFWMATCH-26SEP22OZXLEW-OZX"      # Ipek Oz's side of the women's W50 Plovdiv market


# ── the rule fires: venue-confirmed OZX = Ipek Oz -> ok:code_pad ──────────────────────────────────────────────
def test_pad_rule_clears_ipek_oz():
    assert AUD("itf", "Ipek Oz", OZX, "yes") == "ok:code_pad"


def test_pad_rule_clears_other_two_letter_surnames_first_last():
    # the same convention across the 18+ current ITF two-letter surnames (Polymarket first-last order)
    assert AUD("itf", "Jiayu Xu", "KXITFWMATCH-26SEP22XUXABC-XUX", "yes") == "ok:code_pad"
    assert AUD("itf", "Ru Xi Wu", "KXITFWMATCH-26SEP22WUXABC-WUX", "yes") == "ok:code_pad"
    assert AUD("itf", "Connie Ma", "KXITFWMATCH-26SEP22MAXABC-MAX", "yes") == "ok:code_pad"
    assert AUD("itf", "Chan-Yeong Oh", "KXITFMATCH-26SEP22OHXABC-OHX", "yes") == "ok:code_pad"   # accent/hyphen fold


# ── anti-cases: each MUST stay flagged (code_review) or not match the rule ─────────────────────────────────────
def test_opponent_on_same_market_stays_flagged():
    # the wrong-side case, and the whole reason the audit exists: OZX ticker but the whale outcome is the OPPONENT
    assert AUD("itf", "Astrid Lew Yan Foon", OZX, "yes").startswith("code_review")


def test_different_two_letter_surname_stays_flagged():
    # code OZX but the outcome's last token is a DIFFERENT two-letter surname -> equality fails -> flagged
    assert AUD("itf", "Jiayu Xu", OZX, "yes").startswith("code_review")


def test_surname_first_name_order_stays_flagged_safe_direction():
    # "Xu Yifan" (surname first): last token "yifan" != "xu" -> the rule MISSES -> stays flagged. A false flag,
    # never a false clear -- the safe direction, deliberately kept (not loosened to "any token equals").
    assert AUD("itf", "Xu Yifan", "KXITFWMATCH-26SEP22XUXABC-XUX", "yes").startswith("code_review")


def test_atp_code_ending_x_never_matches():
    # ATP codes derive from the FIRST name -- the rule must NEVER touch them, even if last-token == code[:2]
    assert AUD("atp", "Zoe Ab", "KXATPMATCH-26SEP22ABXZZZ-ABX", "yes").startswith("code_review")


def test_wta_code_ending_x_never_matches():
    assert AUD("wta", "Zoe Ab", "KXWTAMATCH-26SEP22ABXZZZ-ABX", "yes").startswith("code_review")


def test_non_tennis_category_never_matches():
    assert AUD("cs2", "Zoe Ab", "KXCS2GAME-26SEP22ABXZZZ-ABX", "yes").startswith("code_review")


def test_code_not_exactly_three_chars_never_matches():
    # a disambiguated 4-char code (OHX2) is NOT the 3-char pad shape -> rule misses -> stays flagged
    assert AUD("itf", "Jeongha Oh", "KXITFWMATCH-26SEP22OHX2ABC-OHX2", "yes").startswith("code_review")


def test_code_not_ending_in_x_never_matches():
    assert AUD("itf", "Zy Ab", "KXITFMATCH-26SEP22ABCZZZ-ABC", "yes").startswith("code_review")


# ── existing behavior is unchanged by the new branch ──────────────────────────────────────────────────────────
def test_subsequence_pass_still_ok():
    assert AUD("itf", "Han Shi", "KXITFWMATCH-26SEP22SHIABC-SHI", "yes") == "ok"   # 'shi' IS a subsequence


def test_code_alias_still_clears():
    assert AUD("cs2", "Luminosity", "KXCS2GAME-26SEP22LGABC-LG", "yes") == "ok:code_alias"


def test_genuine_code_review_unchanged():
    assert AUD("cs2", "Some Squad", "KXCS2GAME-26SEP22ZZABC-ZZ", "yes").startswith("code_review")


# ── read side: ok:code_pad is CLEAN and NEVER surfaced on the strip ──────────────────────────────────────────
def test_classify_code_pad_is_clean_not_surfaced():
    assert LA.classify_leg_audit("ok:code_pad") == LA.STATE_CLEAN
    assert LA.STATE_CLEAN not in LA.SURFACED_STATES


def _mini_db():
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE pm_subdivision_order (id INTEGER PRIMARY KEY, account_id TEXT, category TEXT, "
                "ticker TEXT, outcome_leg TEXT, signal_outcome TEXT, signal_slug TEXT, leg_audit TEXT, "
                "outcome_status TEXT, response_ts INTEGER, dry_run INTEGER)")
    rows = [
        (1, "kalshi_jack", "itf", OZX, "yes", "Ipek Oz", "s", "ok:code_pad", "filled", 100, 0),        # cleared
        (2, "kalshi_jack", "cs2", "KXCS2GAME-x-ZZ", "yes", "Sq", "s", "code_review:x!<Sq", "filled", 90, 0),  # soft
    ]
    con.executemany("INSERT INTO pm_subdivision_order VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return con


def test_read_path_code_pad_not_surfaced_but_code_review_is():
    res = LA.read_leg_audit_reviews(_mini_db(), account_ids=["kalshi_jack"])
    tickers = {r["ticker"] for r in res["rows"]}
    assert OZX not in tickers                                    # the ok:code_pad row is CLEAN -> off the strip
    assert "KXCS2GAME-x-ZZ" in tickers                           # the code_review row IS surfaced
    assert res["counts"][LA.STATE_SOFT] == 1
    assert res["total_surfaced"] == 1                            # exactly the one soft row
