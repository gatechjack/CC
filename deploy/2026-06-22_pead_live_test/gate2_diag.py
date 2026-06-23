"""PEAD STEP 3 — GATE 2 pre-placement diagnosis. READ-ONLY. NO order. NO POST.

The Gate-2 order POST came back empty (no RH order created; account stayed clean).
This rebuilds the account / instrument / orders-URL resolution that
robin_stocks.robinhood.orders.order() uses to assemble its POST payload — WITHOUT
posting — so we can see which piece comes back empty.

Leading hypothesis (from offline robin_stocks 3.4.0 source):
  account_profile_url(account_number) builds 'accounts/680725082' with NO trailing
  slash. order() sets payload['account'] = load_account_profile(account_number,
  info='url'); if that resolves to None for the cash account 680725082, the POST
  payload is malformed -> empty/failed order. This confirms or refutes it, and the
  slash A/B shows whether the missing trailing slash is the reason.

Mirrors the harness: connects the branch RobinhoodBroker (read-only hard-bind)
first, then runs the resolution checks. Places nothing.
"""
from __future__ import annotations

import asyncio
import json

ACCOUNT = "680725082"
SYM = "F"


async def _amain() -> int:
    import robin_stocks.robinhood as rs
    from robin_stocks.robinhood.urls import account_profile_url, orders_url

    from trading_corp.brokers.robinhood import RobinhoodBroker
    from trading_corp.utils.secrets import load_secrets

    secrets = load_secrets()
    broker = RobinhoodBroker(
        username=secrets.robinhood_username,
        password=secrets.robinhood_password,
        mfa_secret=secrets.robinhood_mfa_secret,
        account_filter=ACCOUNT,
    )
    await broker.connect()
    print(f"broker bound account = {getattr(broker, '_account_number', '')!r}\n")

    # 1) the URL robin_stocks builds to resolve the account (note: NO trailing slash)
    print(f"account_profile_url({ACCOUNT}) = {account_profile_url(ACCOUNT)}")

    # 2) THE KEY CHECK — exactly what order() assigns to payload['account']
    acct_url = rs.profiles.load_account_profile(account_number=ACCOUNT, info="url")
    print(f"load_account_profile({ACCOUNT}, info='url') = {acct_url!r}   <-- payload['account']")
    prof = rs.profiles.load_account_profile(account_number=ACCOUNT)
    if isinstance(prof, dict):
        print(f"  profile: account_number={prof.get('account_number')} type={prof.get('type')} "
              f"url={prof.get('url')}")
    else:
        print(f"  profile (non-dict/empty): {prof!r}")

    # 3) trailing-slash A/B — does the no-slash URL actually fail?
    for tag, url in (("no-slash", f"https://api.robinhood.com/accounts/{ACCOUNT}"),
                     ("slash   ", f"https://api.robinhood.com/accounts/{ACCOUNT}/")):
        try:
            d = rs.helper.request_get(url)
            acct = d.get("account_number") if isinstance(d, dict) else None
            print(f"GET {tag} -> non-empty={bool(d)} account_number={acct}")
        except Exception as e:  # noqa: BLE001
            print(f"GET {tag} -> ERROR {type(e).__name__}: {e}")

    # 4) the rest of the payload pieces order() builds
    try:
        instr = rs.stocks.get_instruments_by_symbols(SYM, info="url")
        print(f"get_instruments_by_symbols({SYM!r}, info='url') = {instr}")
    except Exception as e:  # noqa: BLE001
        print(f"get_instruments_by_symbols error: {e}")
    print(f"orders_url(account_number={ACCOUNT}) = {orders_url(account_number=ACCOUNT)}")

    print("\nVERDICT:")
    if not acct_url:
        print(f"  ROOT CAUSE -> load_account_profile({ACCOUNT}) returned no url. order() would set")
        print("  payload['account']=None -> malformed/empty POST. See the slash A/B for why.")
    else:
        print(f"  account RESOLVES to {acct_url} -> payload['account'] is fine; the empty POST")
        print("  cause is elsewhere (the POST/request_post itself or another payload field).")
    print("=== END DIAG — nothing placed ===")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
