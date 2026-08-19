# pk_markprobe_ro.ps1 -- READ-ONLY: dump the raw Kalshi orderbook + MarketModel for a live
# KXMLBGAME ticker to disambiguate empty-book vs attribute-mismatch as the cause of the mark
# poller's 2029/2029 quote_miss. Reproduces the live KalshiBroker.quote()==0.0 bug and prints the
# WORKING MarketModel top-of-book path (the slippage guard's source). NO order, NO mutation, does
# not touch the live loop. Best run during an MLB game window (live book).
# Run:
#   powershell -ep bypass -f .\pk_markprobe_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import asyncio, os
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass

async def main():
    import pykalshi
    from pykalshi import MarketStatus
    from trading_corp.utils.secrets import load_secrets
    from trading_corp.brokers.kalshi_live import KalshiLiveBroker
    print("pykalshi.__version__ =", getattr(pykalshi, "__version__", "?"))
    s = load_secrets()
    b = KalshiLiveBroker(api_key_id=s.kalshi_karen_api_key_id,
                         private_key_pem=s.kalshi_karen_private_key_pem,
                         demo=False, order_type="ioc", max_slippage_cents=2)
    await b.connect()
    client = b._read._client
    if client is None:
        print("CLIENT IS NONE (stub mode) -- KAREN creds missing; abort"); return
    tickers = []
    try:
        ms = await client.get_markets(series_ticker="KXMLBGAME", status=MarketStatus.OPEN, limit=8)
        tickers = [getattr(m, "ticker", "") or "" for m in (ms or [])]
    except Exception as e:
        print("get_markets(OPEN) failed:", e)
    tickers = [t for t in tickers if t]
    print("OPEN KXMLBGAME sample =", tickers[:8])
    if not tickers:
        try:
            from trading_corp.persistence import db
            DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
            with db.connect(DB) as c:
                rows = c.execute("select distinct json_extract(payload_json,'$.ticker') from audit_event where actor='poly_kalshi_mlb' and kind='poly_kalshi_order' and json_extract(payload_json,'$.status')='placed'").fetchall()
            tickers = [r[0] for r in rows if r and r[0]]
            print("fallback open-position tickers =", tickers[:8])
        except Exception as e:
            print("fallback db lookup failed:", e)
    if not tickers:
        print("NO ACTIVE TICKER -- rerun during an MLB game window"); await b.disconnect(); return
    tk = tickers[0]
    print("=== PROBE TICKER:", tk, "===")
    m = await client.get_market(tk)
    print("type(market) =", type(m))
    for a in ("yes_ask_dollars","yes_bid_dollars","yes_ask","yes_bid","no_ask","no_bid","last_price","status"):
        print("  market.%s =" % a, repr(getattr(m, a, "<MISSING>")))
    for depth in (1, 32):
        try:
            ob = await m.get_orderbook(depth=depth)
        except Exception as e:
            print("get_orderbook(depth=%d) FAILED:" % depth, e); continue
        print("--- orderbook depth=%d ---" % depth)
        print("  type =", type(ob))
        print("  repr[:800] =", repr(ob)[:800])
        print("  public dir =", [x for x in dir(ob) if not x.startswith("_")])
        d = getattr(ob, "__dict__", None)
        if d is not None:
            print("  __dict__ keys =", list(d.keys()))
        for a in ("yes_bids","yes_asks","no_bids","no_asks","yes","no","bids","asks","orderbook","book"):
            print("    ob.%s =" % a, repr(getattr(ob, a, "<MISSING>")))
    q = await b._read.quote(tk)
    print("LIVE KalshiBroker.quote(tk) =", q, "  (0.0 == the bug reproduced)")
    ya = getattr(m, "yes_ask_dollars", None); yb = getattr(m, "yes_bid_dollars", None)
    print("WORKING MarketModel path: yes_ask_dollars=%r yes_bid_dollars=%r" % (ya, yb))
    await b.disconnect()
    print("=== DONE (read-only, no order placed) ===")

asyncio.run(main())
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== poly_kalshi mark-poller ORDERBOOK PROBE (READ-ONLY): empty-book vs attribute-mismatch =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
