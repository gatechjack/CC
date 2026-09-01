"""pm_web authz -- the R5 FAIL-CLOSED admin gate (multi-account foundation, 2026-09-01). Pure, fixture-free,
self-runnable. The load-bearing assertion: header absent OR PM_ADMIN_IDENTITIES unset => NOT admin, never admin
(an unwired identity layer must default to DENY). Also: identity extraction across the Authelia header spellings."""
from trading_corp.prediction_markets.web import authz


def test_parse_admin_identities():
    assert authz.parse_admin_identities(None) == frozenset()          # unset -> empty (fail-closed)
    assert authz.parse_admin_identities("") == frozenset()
    assert authz.parse_admin_identities("jack") == frozenset({"jack"})
    assert authz.parse_admin_identities("jack, karen") == frozenset({"jack", "karen"})
    assert authz.parse_admin_identities("jack karen") == frozenset({"jack", "karen"})


def test_identity_from_headers_spellings():
    assert authz.identity_from_headers({"Remote-User": "jack"}) == "jack"
    assert authz.identity_from_headers({"X-Forwarded-User": "karen"}) == "karen"
    assert authz.identity_from_headers({"X-Remote-User": "jack"}) == "jack"
    assert authz.identity_from_headers({}) is None                    # no header -> None
    assert authz.identity_from_headers(None) is None
    assert authz.identity_from_headers({"Remote-User": "  "}) is None  # whitespace -> None


def test_is_admin_identity_fail_closed():
    admins = frozenset({"jack"})
    assert authz.is_admin_identity("jack", admins) is True
    assert authz.is_admin_identity("karen", admins) is False          # authenticated but not admin
    # ★ the fail-closed cases -- every one must be False:
    assert authz.is_admin_identity(None, admins) is False             # no identity (header absent)
    assert authz.is_admin_identity("jack", frozenset()) is False      # config unset -> nobody admin
    assert authz.is_admin_identity(None, frozenset()) is False        # both -> False
    assert authz.is_admin_identity("", admins) is False               # empty identity


class _Req:
    def __init__(self, headers):
        self.headers = headers


def test_is_admin_request_wiring(monkeypatch=None):
    import os
    # admin set from env; identity from header
    os.environ["PM_ADMIN_IDENTITIES"] = "jack, karen"
    assert authz.is_admin(_Req({"Remote-User": "jack"})) is True
    assert authz.is_admin(_Req({"Remote-User": "mallory"})) is False  # authenticated, not listed
    assert authz.is_admin(_Req({})) is False                          # no identity header
    # config unset -> nobody admin even with a valid-looking identity
    os.environ.pop("PM_ADMIN_IDENTITIES", None)
    assert authz.is_admin(_Req({"Remote-User": "jack"})) is False


def test_visible_account_ids_scoping_fail_closed():
    accts = [{"account_id": "kalshi_jack", "owner_identity": None},        # unowned -> admin-only
             {"account_id": "kalshi_karen", "owner_identity": "karen"}]    # owned by karen
    # ADMIN sees ALL (incl the unowned)
    assert authz.visible_account_ids("jack", True, accts) == {"kalshi_jack", "kalshi_karen"}
    # non-admin KAREN sees ONLY her account (the NULL-owner jack account is admin-only)
    assert authz.visible_account_ids("karen", False, accts) == {"kalshi_karen"}
    # a non-admin nobody-owns-for -> nothing
    assert authz.visible_account_ids("mallory", False, accts) == set()
    # ★ the fail-closed forks:
    assert authz.visible_account_ids(None, False, accts) == set()          # no identity -> nothing
    assert authz.visible_account_ids("", False, accts) == set()            # empty identity -> nothing
    assert authz.visible_account_ids("karen", False, []) == set()          # no accounts -> nothing
    # a NULL-owner account is NEVER visible to a non-admin even if their identity is also None-ish
    assert authz.visible_account_ids(None, False, [{"account_id": "x", "owner_identity": None}]) == set()


def test_visible_accounts_request_wrapper():
    os_env = __import__("os").environ
    os_env["PM_ADMIN_IDENTITIES"] = "jack"
    accts = [{"account_id": "kalshi_jack", "owner_identity": None},
             {"account_id": "kalshi_karen", "owner_identity": "karen"}]
    assert authz.visible_accounts(_Req({"Remote-User": "jack"}), accts) == {"kalshi_jack", "kalshi_karen"}   # admin: all
    assert authz.visible_accounts(_Req({"Remote-User": "karen"}), accts) == {"kalshi_karen"}                 # scoped
    assert authz.visible_accounts(_Req({}), accts) == set()                                                  # no header -> nothing
    os_env.pop("PM_ADMIN_IDENTITIES", None)
    # ★ config unset -> jack is NOT admin -> he owns no account (his has NULL owner) -> sees NOTHING. This IS the
    # lockout risk: the admin env MUST be set (with jack's identity) BEFORE the scoping enforces, or he loses the console.
    assert authz.visible_accounts(_Req({"Remote-User": "jack"}), accts) == set()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print("ALL %d PASS" % len(fns))
