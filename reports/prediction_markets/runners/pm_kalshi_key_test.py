"""READ-ONLY Kalshi key AUTH test -- existing key vs Jack's new Karen-TWO key (investigation pt2, Task 1b+2).

Uses the read-only KalshiBroker (a ReadOnlyBroker with NO place_order -- placing an order is a static
impossibility, not a runtime guard). Fetches keys from KeyVault BY EXACT NAME via the VM Managed Identity.
NEVER prints a secret value / prefix / length -- only retrieved yes/no, and the auth/read RESULT. The
decisive test is get_balance (an authenticated portfolio read): if the existing key 401s and the new key
succeeds, the block is a dead key, not geolocation.
"""
import asyncio
import logging
import os

logging.basicConfig(level=logging.ERROR)  # no INFO/DEBUG -> no third-party secret logging; our prints only


def _fetch(client, name):
    try:
        v = client.get_secret(name).value
        return v, bool(v)
    except Exception as e:  # noqa: BLE001
        print("  KV fetch FAILED  %-30s %s: %s" % (name, type(e).__name__, str(e)[:120]))
        return None, False


async def _test_key(label, key_id, pem, api_base):
    from trading_corp.brokers.kalshi import KalshiBroker
    host = api_base or "default (api.elections.kalshi.com)"
    print("\n-- %s | host=%s --" % (label, host))
    b = KalshiBroker(api_key_id=key_id, private_key_pem=pem, demo=False, api_base=api_base)
    try:
        await b.connect()
    except Exception as e:  # noqa: BLE001
        print("   connect FAILED: %s: %s" % (type(e).__name__, str(e)[:200]))
        try:
            await b.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return
    # DECISIVE: authenticated portfolio read. Raw (not b.snapshot(), which swallows the 401 into zeros).
    try:
        bal = await b._client.portfolio.get_balance()
        print("   AUTH OK   get_balance -> balance_cents=%s portfolio_cents=%s"
              % (getattr(bal, "balance", "?"), getattr(bal, "portfolio_value", "?")))
    except Exception as e:  # noqa: BLE001
        print("   AUTH FAIL get_balance -> %s: %s" % (type(e).__name__, str(e)[:220]))
    # BONUS: a market read via the same client (reads are public; confirms the read path with this key).
    try:
        m = await b._client.get_market("KXMLBGAME-26AUG262105MINATH-MIN")
        print("   READ get_market ok (status=%s)" % (getattr(m, "status", "?"),))
    except Exception as e:  # noqa: BLE001
        print("   READ get_market -> %s: %s (best-effort ticker; not an auth signal)"
              % (type(e).__name__, str(e)[:140]))
    await b.disconnect()


async def _main():
    uri = os.environ.get("KEY_VAULT_URI")
    if not uri:
        print("KEY_VAULT_URI not set -- cannot reach KeyVault as this user. STOP.")
        return
    print("KEY_VAULT_URI present (from engine service env).")
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        client = SecretClient(vault_url=uri, credential=DefaultAzureCredential())
    except Exception as e:  # noqa: BLE001
        print("KeyVault client init FAILED: %s: %s" % (type(e).__name__, str(e)[:200]))
        return

    # Test all three candidate keys so we can tell WHICH the engine uses + whether it is the dead one:
    #  KALSHI (original), KALSHI-KAREN (the "one" key the poly_kalshi division may use), Karen-TWO (Jack's new).
    keys = []
    for label, id_name, pem_name in (
        ("KALSHI (original)",   "KALSHI-API-KEY-ID",           "KALSHI-PRIVATE-KEY-PEM"),
        ("KALSHI-KAREN (one)",  "KALSHI-KAREN-API-KEY-ID",     "KALSHI-KAREN-PRIVATE-KEY-PEM"),
        ("Karen-TWO (new)",     "Kalshi-Karen-TWO-API-KEY",    "Karen-Kalshi-TWO-PRIVATE-KEY"),
    ):
        kid, ok_id = _fetch(client, id_name)
        pem, ok_pem = _fetch(client, pem_name)
        print("%-20s retrieved: id=%s pem=%s" % (label, ok_id, ok_pem))
        if ok_id and ok_pem:
            keys.append((label, kid, pem))

    ext = "https://external-api.kalshi.com/trade-api/v2"   # the ORDER host where poly_kalshi 401s
    for base in (ext, None):
        for (label, kid, pem) in keys:
            await _test_key(label, kid, pem, base)
    print("\n== END key auth test ==")


asyncio.run(_main())
