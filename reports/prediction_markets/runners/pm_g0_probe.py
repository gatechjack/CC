#!/usr/bin/env python3
# pm_g0_probe.py -- Prediction Markets P1 G0 validation gate (READ-ONLY).
# Proves data-api.polymarket.com/closed-positions returns NEGATIVE realizedPnl rows
# for known net-loser wallets, disproving the "positives-only survivorship" concern
# (legacy claim in seed_polymarket_watchlist_deep.py:57-62). Pure stdlib (urllib),
# public API, no auth. NO DB writes, no engine touch, no box state change.
# Canonical probe source; the delivery vehicle is pk_g0_probe_ro.ps1 (inlines this).
import json
import urllib.request

DATA = "https://data-api.polymarket.com"


def http(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "pm-g0/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.getcode(), json.loads(r.read().decode())


def closed(w, limit=50, offset=0):
    return http("%s/closed-positions?user=%s&limit=%d&offset=%d" % (DATA, w, limit, offset))


def pull_all(w, cap=8000):
    rows = []
    for off in range(0, cap, 50):
        try:
            _, r = closed(w, 50, off)
        except Exception as e:
            print("   PULL ERROR at offset %d: %s" % (off, str(e)[:90]))
            break
        if not r:
            break
        rows.extend(r)
        if len(r) < 50:
            break
    return rows


def fnum(x):
    try:
        return float(x)
    except Exception:
        return 0.0


LOSERS = [
    ("evanng(UFC)", "0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618", -13706.51),
    ("csgod(UFC)", "0x8056189d56833ce5b3945dea9149b62c5111b64d", -9551.47),
    ("d1k21(Fed)", "0x71ed0bc95433cdf1be29f43219725fce9addd9eb", -168183.81),
]


def main():
    print("===== PREDICTION MARKETS G0 GATE: negative realizedPnl in /closed-positions =====")
    overall = True
    for name, w, exp in LOSERS:
        rows = pull_all(w)
        n = len(rows)
        neg = [r for r in rows if fnum(r.get("realizedPnl")) < 0]
        pos = [r for r in rows if fnum(r.get("realizedPnl")) > 0]
        zero = n - len(neg) - len(pos)
        net = sum(fnum(r.get("realizedPnl")) for r in rows)
        tb = sum(fnum(r.get("totalBought")) for r in rows)
        roi = (net / tb * 100.0) if tb > 0 else 0.0
        wp = len(neg) > 0
        overall = overall and wp
        print("")
        print("-- %s  wallet=%s" % (name, w))
        print("   total_closed_positions=%d  negative=%d  positive=%d  zero=%d" % (n, len(neg), len(pos), zero))
        print("   net_realizedPnl=%.2f  total_bought=%.2f  net_roi=%.1f%%  (activity-method expected net ~ %.2f)"
              % (net, tb, roi, exp))
        print("   WALLET G0: %s" % ("PASS - negative rows present" if wp
                                     else "FAIL - NO negative rows (survivorship concern CONFIRMED)"))
        for r in sorted(neg, key=lambda r: fnum(r.get("realizedPnl")))[:3]:
            t = (r.get("title") or r.get("eventSlug") or r.get("slug") or "?")
            print("     NEG sample: realizedPnl=%.2f curPrice=%s  %s"
                  % (fnum(r.get("realizedPnl")), r.get("curPrice"), t[:58]))

    print("")
    print("===== ORDERING STABILITY PROBE (same wallet, page0 pulled twice) =====")
    try:
        w0 = LOSERS[0][1]
        _, a = closed(w0, 50, 0)
        _, b = closed(w0, 50, 0)
        ka = [x.get("conditionId") for x in a]
        kb = [x.get("conditionId") for x in b]
        print("   page0 len a=%d b=%d  identical_order=%s" % (len(ka), len(kb), str(ka == kb)))
    except Exception as e:
        print("   ordering probe error: %s" % str(e)[:90])

    print("")
    print("===== VERDICT: G0 %s =====" % ("PASS" if overall else "FAIL -- STOP AND REPORT TO JACK"))


if __name__ == "__main__":
    main()
