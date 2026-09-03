set -u
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
date -u +"### UFC MARKET-SHAPE PROBE (READ-ONLY, no creds, PUBLIC market-data GET only) %Y-%m-%dT%H:%M:%SZ ###"
echo "box_hostname: $(hostname)"
echo "Purpose: does the pykalshi get_markets Market OBJECT expose .title (+ every other field the UFC ctx builder needs), or is it DROPPED by the SDK and only in raw /markets? Report BOTH sources + deprecation."
PYTHONPATH="$ROOT" "$V" - <<'PY'
import json, urllib.request, urllib.error, importlib, pkgutil

# ============ [C] RAW public /markets payload (source of truth: what Kalshi returns) ============
HOSTS = ["https://api.elections.kalshi.com/trade-api/v2",
         "https://external-api.kalshi.com/trade-api/v2"]
def get(host, series, status):
    url = "%s/markets?series_ticker=%s&status=%s&limit=50" % (host, series, status)
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.4.0"})
    try:
        r = urllib.request.urlopen(req, timeout=25)
        return getattr(r, "status", 200), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return "ERR:%r" % e, {}

raw_samples = {}   # series -> (status, host, [markets])
print("=== [C] RAW public /markets availability ===")
for series in ("KXUFCFIGHT", "KXUFCDISTANCE"):
    picked = False
    for host in HOSTS:
        hostlbl = host.split("//")[1].split(".")[0]
        for status in ("open", "settled"):
            code, data = get(host, series, status)
            mk = (data.get("markets") if isinstance(data, dict) else None) or []
            print("  %s host=%-14s status=%-7s http=%s count=%d" % (series, hostlbl, status, code, len(mk)))
            if mk and not picked:
                raw_samples[series] = (status, hostlbl, mk); picked = True
        if picked:
            break

for series, (status, hostlbl, mk) in raw_samples.items():
    s = mk[0]
    print()
    print("=== [C] RAW SAMPLE %s (status=%s host=%s, %d markets) ===" % (series, status, hostlbl, len(mk)))
    print("  ALL RAW KEYS:", sorted(s.keys()))
    for k in ("ticker","title","subtitle","yes_sub_title","no_sub_title","yes_bid","yes_ask",
              "no_bid","no_ask","last_price","previous_price","yes_bid_dollars","yes_ask_dollars",
              "liquidity","liquidity_dollars","open_interest","volume","notional_value",
              "exchange_index","status","market_type","close_time","expiration_time","event_ticker"):
        if k in s:
            print("   raw[%-18s] = %r" % (k, s.get(k)))
    print("   -- titles (first 12, to confirm the '{Full Name} wins' / distance shape) --")
    for m in mk[:12]:
        print("      title=%r  ticker=%s" % (m.get("title"), m.get("ticker")))

# ============ [A] pykalshi Market MODEL: what the SDK OBJECT declares as attributes ============
print()
print("=== [A] pykalshi Market model introspection ===")
try:
    import pykalshi
    print("  pykalshi version:", getattr(pykalshi, "__version__", "?"), " file:", getattr(pykalshi, "__file__", "?"))
except Exception as e:
    print("  import pykalshi FAILED:", repr(e)); pykalshi = None

cands = {}
def scan(mod):
    for n in dir(mod):
        try:
            o = getattr(mod, n, None)
        except Exception:
            continue
        mf = getattr(o, "model_fields", None)
        if isinstance(mf, dict) and "ticker" in mf:
            cands[mod.__name__ + "." + n] = o

if pykalshi is not None:
    scan(pykalshi)
    try:
        for mi in pkgutil.iter_modules(getattr(pykalshi, "__path__", []), pykalshi.__name__ + "."):
            try: scan(importlib.import_module(mi.name))
            except Exception: pass
    except Exception as e:
        print("  submodule scan note:", repr(e))

print("  Market-model candidates (pydantic models with a 'ticker' field):", sorted(cands.keys()))
best = None
for nm, o in cands.items():
    if best is None or len(o.model_fields) > len(best[1].model_fields):
        best = (nm, o)

Model = None
if best:
    nm, Model = best
    mf = Model.model_fields
    cfg = getattr(Model, "model_config", {})
    extra = cfg.get("extra") if isinstance(cfg, dict) else getattr(cfg, "extra", None)
    print("  CHOSEN MODEL: %s  (%d fields)  model_config.extra=%r" % (nm, len(mf), extra))
    print("  (extra='ignore'/None => raw keys NOT declared here are DROPPED by the SDK object)")
    for k in sorted(mf):
        v = mf[k]
        print("   field %-22s type=%-28s deprecated=%s" % (k, str(getattr(v, "annotation", None))[:28], getattr(v, "deprecated", None)))
else:
    print("  NO Market model with a 'ticker' field found -- report and stop, cannot introspect the SDK object shape.")

# ============ [D] feed a REAL raw market THROUGH the model -> what the SDK object actually carries ============
print()
print("=== [D] raw market -> pykalshi Model.model_validate -> what SURVIVES (the exact live-path transform) ===")
if Model is not None and raw_samples:
    for series, (status, hostlbl, mk) in raw_samples.items():
        try:
            obj = Model.model_validate(mk[0])
            dumped = obj.model_dump()
            print("  --- %s ---" % series)
            print("    model_dump keys:", sorted(dumped.keys()))
            for a in ("ticker","title","subtitle","yes_sub_title","yes_bid","yes_ask","no_bid","no_ask",
                      "yes_bid_dollars","yes_ask_dollars","no_bid_dollars","no_ask_dollars",
                      "liquidity_dollars","exchange_index","close_time","expiration_time"):
                val = getattr(obj, a, "<<NO-ATTR>>")
                print("     obj.%-18s = %-40r in_model_dump=%s" % (a, val, a in dumped))
        except Exception as e:
            print("  MODEL-VALIDATE %s FAILED: %r" % (series, e))
elif Model is None:
    print("  (skipped: no model)")
else:
    print("  (skipped: no raw UFC markets available right now to validate -- re-probe near a fight card)")
print()
print("### DONE ###")
PY
