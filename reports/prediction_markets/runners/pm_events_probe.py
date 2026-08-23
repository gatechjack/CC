#!/usr/bin/env python3
# pm_events_probe.py -- READ-ONLY: capture the gamma /events TAGS schema for tier-2 (events-tag-join).
# (a)/(c) gamma /events?slug=<eventSlug> tags per sample (MLB/UFC/NBA/Fed + unknown tail); (b) whether
# tags are embedded in /markets events[]; + futures-vs-single-game discriminator (gameStartTime /
# sportsMarketType / negRisk). Emits RAWEVENT lines (slim event, markets[] stripped) for offline fixtures.
# Pure stdlib. Public no-auth APIs. NO writes to box state.
import json
import urllib.request

DATA = "https://data-api.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"


def http(u):
    r = urllib.request.Request(u, headers={"User-Agent": "pm-events-probe/1.0"})
    with urllib.request.urlopen(r, timeout=30) as x:
        return json.loads(x.read().decode())


PREF = ["fed-decision", "fed-interest-rates", "fed-rate", "fed", "mlb", "nba", "nfl",
        "nhl", "ufc", "cs2", "atp", "wta", "cbb", "fifwc", "epl", "ucl", "wnba", "nascar"]


def catof(es):
    s = (es or "").lower()
    for p in sorted(PREF, key=len, reverse=True):
        if s == p or s.startswith(p + "-"):
            return "fed" if p.startswith("fed") else p
    return "unknown"


WALLETS = ["0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618", "0x71edffd0d70a1da823ff07a3c6fc81457294d338"]
seen = {}
for w in WALLETS:
    for off in range(0, 800, 50):
        try:
            rows = http("%s/closed-positions?user=%s&limit=50&offset=%d" % (DATA, w, off))
        except Exception as e:
            print("pull err", str(e)[:60])
            break
        if not rows:
            break
        for r in rows:
            es = r.get("eventSlug") or ""
            cid = r.get("conditionId")
            if es and cid and es not in seen:
                seen[es] = (cid, catof(es))
        if len(rows) < 50:
            break

bycat = {}
for es, (cid, c) in seen.items():
    bycat.setdefault(c, []).append((es, cid))
picks = []
for c in ("mlb", "ufc", "nba", "fed"):
    if bycat.get(c):
        picks.append((c, bycat[c][0][0], bycat[c][0][1]))
for es, cid in bycat.get("unknown", [])[:4]:
    picks.append(("unknown", es, cid))

print("PICKS (cat, eventSlug, cid):")
for c, es, cid in picks:
    print("  [%s] %s cid=%s" % (c, es, cid[:20]))
print("")

print("===== (a)/(c) GAMMA /events?slug=<eventSlug> -> TAGS SCHEMA =====")
for c, es, cid in picks:
    try:
        evs = http("%s/events?slug=%s" % (GAMMA, es))
    except Exception as e:
        print("--- [%s] %s ERR %s" % (c, es, str(e)[:60]))
        continue
    ev = evs[0] if (isinstance(evs, list) and evs) else (evs if isinstance(evs, dict) else {})
    print("--- [%s] eventSlug=%s ---" % (c, es))
    print("  event keys:", sorted(ev.keys()))
    print("  tags:", json.dumps(ev.get("tags"), default=str)[:900])
    slim = {k: v for k, v in ev.items() if k != "markets"}
    print("RAWEVENT:%s:%s:%s" % (c, es, json.dumps(slim, default=str)))
print("")

print("===== (b)+DISCRIMINATOR /markets per pick: events[].tags? + futures-vs-game =====")
for c, es, cid in picks:
    try:
        ms = http("%s/markets?condition_ids=%s&closed=true" % (GAMMA, cid))
        m = ms[0] if ms else {}
    except Exception as e:
        print("[%s] %s ERR %s" % (c, es, str(e)[:50]))
        continue
    mev = m.get("events")
    ev0 = mev[0] if (isinstance(mev, list) and mev and isinstance(mev[0], dict)) else {}
    print("--- [%s] cid=%s ---" % (c, cid[:16]))
    print("  gameStartTime=%s  sportsMarketType=%s  negRisk=%s" % (m.get("gameStartTime"), m.get("sportsMarketType"), m.get("negRisk")))
    print("  market.events[0] has tags:", ("tags" in ev0), " tags:", json.dumps(ev0.get("tags"), default=str)[:500])
print("")
print("PROBE DONE")
