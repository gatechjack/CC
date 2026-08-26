"""CP2 Phase-3 display-name population (names.sync_user_names). Offline; tmp DB only.

Encodes the Board rulings on Option A: it is a POPULATION step (not a join), re-runnable +
idempotent, records WHEN it last ran (staleness diagnosable), keyed on WALLET (a display-name
collision NEVER merges two whales), never clobbers a real name with an empty roster label, and the
read path (last_sync) does not create pm_meta -> a web GET stays pure. Schema stays at v4 (pm_meta is
an ops table outside the numbered-migration chain).

Spec: reports/prediction_markets/P3_KICKOFF_2026-08-24.md.
"""
from trading_corp.prediction_markets import db, names

NOW = 1_700_000_000


def _whale(conn, wallet, name=None):
    conn.execute(
        "INSERT INTO pm_whale (wallet, user_name, first_seen_ts, backfill_complete) VALUES (?,?,?,1)",
        (wallet, name, NOW))


def _init(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


def test_populates_user_name_from_roster(tmp_path):
    p = _init(tmp_path)
    with db.connect(p) as conn:
        _whale(conn, "0xaaa"); _whale(conn, "0xbbb")
        counts = names.sync_user_names(conn, [
            {"wallet": "0xAAA", "user_name": "Kickstand7"},   # roster label upper-cased wallet -> lowercased
            {"wallet": "0xbbb", "user_name": "BetMechanic"},
        ], now_ts=NOW)
        got = {r["wallet"]: r["user_name"]
               for r in conn.execute("SELECT wallet, user_name FROM pm_whale")}
    assert got == {"0xaaa": "Kickstand7", "0xbbb": "BetMechanic"}
    assert counts["n_set"] == 2 and counts["n_matched"] == 2 and counts["n_whales_unnamed_after"] == 0


def test_idempotent_second_run_is_noop(tmp_path):
    p = _init(tmp_path)
    roster = [{"wallet": "0xaaa", "user_name": "Kickstand7"}]
    with db.connect(p) as conn:
        _whale(conn, "0xaaa")
        c1 = names.sync_user_names(conn, roster, now_ts=NOW)
        c2 = names.sync_user_names(conn, roster, now_ts=NOW + 1)
        name = conn.execute("SELECT user_name FROM pm_whale WHERE wallet='0xaaa'").fetchone()[0]
    assert c1["n_set"] == 1 and c1["n_changed"] == 0
    assert c2["n_set"] == 0 and c2["n_unchanged"] == 1        # re-run writes nothing
    assert name == "Kickstand7"


def test_rename_is_counted_as_changed(tmp_path):
    # a whale renamed on Polymarket: the roster now carries a different label -> n_changed surfaces it
    p = _init(tmp_path)
    with db.connect(p) as conn:
        _whale(conn, "0xaaa", name="OldName")
        c = names.sync_user_names(conn, [{"wallet": "0xaaa", "user_name": "NewName"}], now_ts=NOW)
        name = conn.execute("SELECT user_name FROM pm_whale WHERE wallet='0xaaa'").fetchone()[0]
    assert c["n_set"] == 1 and c["n_changed"] == 1 and name == "NewName"


def test_wallet_is_identity_name_collision_does_not_merge(tmp_path):
    # TWO wallets that share a display name stay TWO distinct whales (wallet is the identity, never the name)
    p = _init(tmp_path)
    with db.connect(p) as conn:
        _whale(conn, "0xaaa"); _whale(conn, "0xbbb")
        names.sync_user_names(conn, [
            {"wallet": "0xaaa", "user_name": "SameName"},
            {"wallet": "0xbbb", "user_name": "SameName"},
        ], now_ts=NOW)
        rows = conn.execute("SELECT wallet, user_name FROM pm_whale ORDER BY wallet").fetchall()
    assert len(rows) == 2                                     # NOT merged
    assert rows[0]["wallet"] == "0xaaa" and rows[1]["wallet"] == "0xbbb"
    assert rows[0]["user_name"] == rows[1]["user_name"] == "SameName"


def test_empty_roster_label_never_clobbers_existing_name(tmp_path):
    p = _init(tmp_path)
    with db.connect(p) as conn:
        _whale(conn, "0xaaa", name="Keeper")
        names.sync_user_names(conn, [{"wallet": "0xaaa", "user_name": ""}], now_ts=NOW)   # empty label
        name = conn.execute("SELECT user_name FROM pm_whale WHERE wallet='0xaaa'").fetchone()[0]
    assert name == "Keeper"                                  # bias-down: never overwrite a real name with empty


def test_missing_name_stays_null(tmp_path):
    # a whale absent from the roster keeps NULL user_name -> the page renders the WALLET, not a placeholder
    p = _init(tmp_path)
    with db.connect(p) as conn:
        _whale(conn, "0xaaa"); _whale(conn, "0xnobody")
        counts = names.sync_user_names(conn, [{"wallet": "0xaaa", "user_name": "Named"}], now_ts=NOW)
        nm = conn.execute("SELECT user_name FROM pm_whale WHERE wallet='0xnobody'").fetchone()[0]
    assert nm is None
    assert counts["n_whales_unnamed_after"] == 1


def test_update_only_does_not_insert_unbackfilled_wallet(tmp_path):
    # a roster wallet with no pm_whale row (never backfilled) is NOT inserted -- names annotate tracked whales
    p = _init(tmp_path)
    with db.connect(p) as conn:
        _whale(conn, "0xaaa")
        names.sync_user_names(conn, [
            {"wallet": "0xaaa", "user_name": "Tracked"},
            {"wallet": "0xghost", "user_name": "NeverBackfilled"},
        ], now_ts=NOW)
        n = conn.execute("SELECT COUNT(*) FROM pm_whale").fetchone()[0]
        ghost = conn.execute("SELECT COUNT(*) FROM pm_whale WHERE wallet='0xghost'").fetchone()[0]
    assert n == 1 and ghost == 0


def test_records_last_run_and_schema_unbumped_by_pm_meta(tmp_path):
    p = _init(tmp_path)
    with db.connect(p) as conn:
        _whale(conn, "0xaaa")
        names.sync_user_names(conn, [{"wallet": "0xaaa", "user_name": "N"}], now_ts=NOW)
        rec = names.last_sync(conn)
        schema_v = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert rec is not None and rec["last_run_ts"] == NOW and rec["n_set"] == 1
    assert schema_v == 8                                     # pm_meta is OUTSIDE the migration chain -> schema stays at the head (001-008)
    assert "pm_meta" in tables


def test_last_sync_none_when_never_run_does_not_create_meta(tmp_path):
    # a read before any sync returns None and must NOT create pm_meta (web GET read path stays pure)
    p = _init(tmp_path)
    with db.connect(p) as conn:
        rec = names.last_sync(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert rec is None
    assert "pm_meta" not in tables
