"""N1 + N2 (per-account trading) -- the boot-wiring DECISION logic, proven WITHOUT the live POST path (no pykalshi,
no broker, no venue). Covers:
  * N1 -- resolve_kalshi_keys is a fail-CLOSED whitelist (instance #13 of "a safety check that silently stops
    checking"): an unmapped/typo'd secret_ref returns (None, None), NEVER jack's keypair.
  * N2 -- driver_roster.active_driver_subdivisions is the ATTACHMENT-GATED roster, and plan_driver_tasks fails
    closed on no-keys + refuses a 2nd sub-division on one account.
  * ★ RULING 2 CREDENTIAL-BINDING PROOF: from the resolved roster (NOT by inference), Karen's task binds Karen's
    DISTINCT keypair and jack's binds jack's -- the property Karen's first full-size order rests on now that
    place-one-and-inspect is skipped.
"""
import sqlite3
import types

from trading_corp.prediction_markets import driver_roster as DR
from trading_corp.prediction_markets.shard_snapshot_task import resolve_kalshi_keys


# ---- fakes -----------------------------------------------------------------------------------------------------

def _secrets(with_karen=True):
    """A stand-in for the secrets object. Jack and Karen keys are DELIBERATELY DISTINCT sentinel strings so a
    misroute (karen -> jack's keys) is a value mismatch a test can catch, not a silent pass."""
    ns = types.SimpleNamespace(
        kalshi_api_key_id="JACK_KID", kalshi_private_key_pem="JACK_PEM")
    if with_karen:
        ns.kalshi_karen_api_key_id = "KAREN_KID"
        ns.kalshi_karen_private_key_pem = "KAREN_PEM"
    return ns


def _db(accounts, subs, attachments):
    """In-memory PM DB with just the columns active_driver_subdivisions reads.
    accounts    = [(account_id, secret_ref, active)]
    subs        = [(account_id, category, active)]
    attachments = [(account_id, category, wallet, active)]"""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE pm_account (account_id TEXT, secret_ref TEXT, active INT)")
    c.execute("CREATE TABLE pm_subdivision (account_id TEXT, category TEXT, active INT)")
    c.execute("CREATE TABLE pm_subdivision_attachment (account_id TEXT, category TEXT, wallet TEXT, active INT)")
    c.executemany("INSERT INTO pm_account VALUES (?,?,?)", accounts)
    c.executemany("INSERT INTO pm_subdivision VALUES (?,?,?)", subs)
    c.executemany("INSERT INTO pm_subdivision_attachment VALUES (?,?,?,?)", attachments)
    return c


# ---- N1: resolve_kalshi_keys whitelist -------------------------------------------------------------------------

def test_n1_karen_ref_resolves_to_karens_keys():
    assert resolve_kalshi_keys("kalshi_karen", _secrets()) == ("KAREN_KID", "KAREN_PEM")


def test_n1_kalshi_and_kalshi_jack_both_resolve_to_jacks_keys():
    assert resolve_kalshi_keys("KALSHI", _secrets()) == ("JACK_KID", "JACK_PEM")
    assert resolve_kalshi_keys("kalshi_jack", _secrets()) == ("JACK_KID", "JACK_PEM")


def test_n1_UNMAPPED_ref_fails_CLOSED_not_to_jack():
    # The whole point: a typo'd / new / unmapped ref must NOT silently get jack's keypair.
    for bad in ("kalshi_bob", "kalshiKaren", "KALSHI_KAREN", "", None, "kalshi", "karen"):
        assert resolve_kalshi_keys(bad, _secrets()) == (None, None), bad


def test_n1_mapped_ref_but_secret_field_absent_is_None_None():
    # karen ref, but the secrets object lacks karen fields (KeyVault miss) -> (None, None) -> caller skips.
    assert resolve_kalshi_keys("kalshi_karen", _secrets(with_karen=False)) == (None, None)


# ---- N2: active_driver_subdivisions (the attachment-gated roster) -----------------------------------------------

def test_n2_roster_is_INERT_today_only_jack_mlb():
    # Today's live shape: jack active + jack/mlb sub + 1 active attachment; karen account active but NO subdivision.
    c = _db(accounts=[("kalshi_jack", "KALSHI", 1), ("kalshi_karen", "kalshi_karen", 1)],
            subs=[("kalshi_jack", "mlb", 1)],
            attachments=[("kalshi_jack", "mlb", "0xWHALE", 1)])
    assert DR.active_driver_subdivisions(c) == [
        {"account_id": "kalshi_jack", "category": "mlb", "secret_ref": "KALSHI"}]


def test_n2_roster_includes_karen_once_she_has_sub_and_active_attachment():
    c = _db(accounts=[("kalshi_jack", "KALSHI", 1), ("kalshi_karen", "kalshi_karen", 1)],
            subs=[("kalshi_jack", "mlb", 1), ("kalshi_karen", "mlb", 1)],
            attachments=[("kalshi_jack", "mlb", "0xW1", 1), ("kalshi_karen", "mlb", "0xW2", 1)])
    r = DR.active_driver_subdivisions(c)
    assert {(x["account_id"], x["category"], x["secret_ref"]) for x in r} == {
        ("kalshi_jack", "mlb", "KALSHI"), ("kalshi_karen", "mlb", "kalshi_karen")}


def test_n2_pinned_but_UNATTACHED_karen_does_not_enter_roster():
    # A karen/mlb sub EXISTS but has ZERO active attachments -> must NOT spawn a task (it would boot-reconcile the
    # whole account and could latch a category that never trades). This is the load-bearing attachment gate.
    c = _db(accounts=[("kalshi_karen", "kalshi_karen", 1)],
            subs=[("kalshi_karen", "mlb", 1)],
            attachments=[])
    assert DR.active_driver_subdivisions(c) == []


def test_n2_inactive_attachment_only_does_not_enter_roster():
    c = _db(accounts=[("kalshi_karen", "kalshi_karen", 1)],
            subs=[("kalshi_karen", "mlb", 1)],
            attachments=[("kalshi_karen", "mlb", "0xW", 0)])   # attachment present but active=0
    assert DR.active_driver_subdivisions(c) == []


def test_n2_inactive_subdivision_excluded():
    c = _db(accounts=[("kalshi_jack", "KALSHI", 1)],
            subs=[("kalshi_jack", "mlb", 0)],                   # sub inactive
            attachments=[("kalshi_jack", "mlb", "0xW", 1)])
    assert DR.active_driver_subdivisions(c) == []


def test_n2_inactive_account_excluded():
    c = _db(accounts=[("kalshi_jack", "KALSHI", 0)],            # account inactive
            subs=[("kalshi_jack", "mlb", 1)],
            attachments=[("kalshi_jack", "mlb", "0xW", 1)])
    assert DR.active_driver_subdivisions(c) == []


def test_n2_missing_attachment_table_fails_safe_empty():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE pm_account (account_id TEXT, secret_ref TEXT, active INT)")
    c.execute("CREATE TABLE pm_subdivision (account_id TEXT, category TEXT, active INT)")
    c.execute("INSERT INTO pm_account VALUES ('kalshi_jack','KALSHI',1)")
    c.execute("INSERT INTO pm_subdivision VALUES ('kalshi_jack','mlb',1)")
    assert DR.active_driver_subdivisions(c) == []


def test_n2_missing_money_tables_empty():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    assert DR.active_driver_subdivisions(c) == []


# ---- N2: plan_driver_tasks -------------------------------------------------------------------------------------

def test_plan_both_accounts_spawn_when_keyed():
    roster = [{"account_id": "kalshi_jack", "category": "mlb", "secret_ref": "KALSHI"},
              {"account_id": "kalshi_karen", "category": "mlb", "secret_ref": "kalshi_karen"}]
    spawn, skips = DR.plan_driver_tasks(roster, {"kalshi_jack", "kalshi_karen"})
    assert spawn == [{"account_id": "kalshi_jack", "category": "mlb"},
                     {"account_id": "kalshi_karen", "category": "mlb"}]
    assert skips == []


def test_plan_no_keys_skips_that_account_only():
    roster = [{"account_id": "kalshi_jack", "category": "mlb", "secret_ref": "KALSHI"},
              {"account_id": "kalshi_karen", "category": "mlb", "secret_ref": "kalshi_karen"}]
    spawn, skips = DR.plan_driver_tasks(roster, {"kalshi_jack"})   # karen keys absent
    assert spawn == [{"account_id": "kalshi_jack", "category": "mlb"}]
    assert skips == [{"account_id": "kalshi_karen", "category": "mlb", "reason": "no_keys"}]


def test_plan_refuses_second_subdivision_on_one_account():
    roster = [{"account_id": "kalshi_jack", "category": "mlb", "secret_ref": "KALSHI"},
              {"account_id": "kalshi_jack", "category": "nba", "secret_ref": "KALSHI"}]
    spawn, skips = DR.plan_driver_tasks(roster, {"kalshi_jack"})
    assert spawn == [{"account_id": "kalshi_jack", "category": "mlb"}]
    assert skips == [{"account_id": "kalshi_jack", "category": "nba",
                      "reason": "second_subdivision_on_account"}]


def test_plan_distinct_accounts_ok_even_with_a_second_category_elsewhere():
    roster = [{"account_id": "kalshi_jack", "category": "mlb", "secret_ref": "KALSHI"},
              {"account_id": "kalshi_jack", "category": "nba", "secret_ref": "KALSHI"},
              {"account_id": "kalshi_karen", "category": "mlb", "secret_ref": "kalshi_karen"}]
    spawn, skips = DR.plan_driver_tasks(roster, {"kalshi_jack", "kalshi_karen"})
    assert spawn == [{"account_id": "kalshi_jack", "category": "mlb"},
                     {"account_id": "kalshi_karen", "category": "mlb"}]
    assert [s["reason"] for s in skips] == ["second_subdivision_on_account"]


def test_plan_empty_roster():
    assert DR.plan_driver_tasks([], set()) == ([], [])


# ---- ★ RULING 2: end-to-end credential-binding proof (roster -> keys -> plan), no broker/pykalshi --------------

def _decide(conn, secrets):
    """Replicates main.py's N2 decision path PURELY: enumerate roster -> resolve ONE keypair per distinct account
    (fail-closed) -> plan. Returns (spawn, keys_by_account) where keys_by_account is the ACTUAL resolved keypair
    that WOULD build each account's broker. Proving keys_by_account is the correct, DISTINCT keypair per account is
    the credential-path proof Karen's first full-size order rests on."""
    roster = DR.active_driver_subdivisions(conn)
    keys_by_account = {}
    seen = set()
    for r in roster:
        aid = r["account_id"]
        if aid in seen:
            continue
        seen.add(aid)
        kid, pem = resolve_kalshi_keys(r["secret_ref"], secrets)
        if kid and pem:
            keys_by_account[aid] = (kid, pem)
    spawn, skips = DR.plan_driver_tasks(roster, set(keys_by_account))
    return spawn, skips, keys_by_account


def test_ruling2_karen_task_binds_KARENS_keypair_jack_binds_JACKS():
    c = _db(accounts=[("kalshi_jack", "KALSHI", 1), ("kalshi_karen", "kalshi_karen", 1)],
            subs=[("kalshi_jack", "mlb", 1), ("kalshi_karen", "mlb", 1)],
            attachments=[("kalshi_jack", "mlb", "0xW1", 1), ("kalshi_karen", "mlb", "0xW2", 1)])
    spawn, skips, keys = _decide(c, _secrets())
    assert skips == []
    # Each spawned task's account maps to ITS OWN distinct keypair -- proven from the resolved map, not inferred.
    assert keys["kalshi_jack"] == ("JACK_KID", "JACK_PEM")
    assert keys["kalshi_karen"] == ("KAREN_KID", "KAREN_PEM")
    assert keys["kalshi_jack"] != keys["kalshi_karen"]           # no cross-account misroute


def test_ruling2_inert_today_jack_binds_jack_and_no_karen_task():
    # Before Karen has a sub: exactly one task (jack/mlb) on jack's keys -- byte-identical intent to today.
    c = _db(accounts=[("kalshi_jack", "KALSHI", 1), ("kalshi_karen", "kalshi_karen", 1)],
            subs=[("kalshi_jack", "mlb", 1)],
            attachments=[("kalshi_jack", "mlb", "0xW1", 1)])
    spawn, skips, keys = _decide(c, _secrets())
    assert spawn == [{"account_id": "kalshi_jack", "category": "mlb"}]
    assert keys == {"kalshi_jack": ("JACK_KID", "JACK_PEM")}
    assert "kalshi_karen" not in keys


def test_ruling2_karen_with_unmapped_ref_does_NOT_trade_on_jacks_keys():
    # If karen's account were mis-configured with an unmapped secret_ref, she must be SKIPPED, never routed to jack.
    c = _db(accounts=[("kalshi_jack", "KALSHI", 1), ("kalshi_karen", "kalshi_TYPO", 1)],
            subs=[("kalshi_jack", "mlb", 1), ("kalshi_karen", "mlb", 1)],
            attachments=[("kalshi_jack", "mlb", "0xW1", 1), ("kalshi_karen", "mlb", "0xW2", 1)])
    spawn, skips, keys = _decide(c, _secrets())
    assert spawn == [{"account_id": "kalshi_jack", "category": "mlb"}]
    assert "kalshi_karen" not in keys                            # fail-closed, NOT jack's keys
    assert skips == [{"account_id": "kalshi_karen", "category": "mlb", "reason": "no_keys"}]
