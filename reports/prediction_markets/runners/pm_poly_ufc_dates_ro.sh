set -u
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
date -u +"### POLYMARKET UFC SLUG-DATE cross-check v2 (READ-ONLY public gamma GET) %Y-%m-%dT%H:%M:%SZ ###"
echo "Q: does the Poly slug date (ufc-{codes}-YYYY-MM-DD) == the Kalshi ticker card-local date for the SAME fight?"
PYTHONPATH="$ROOT" "$V" - <<'PY'
import json, urllib.request, urllib.error, re
G="https://gamma-api.polymarket.com"
def get(url):
    req=urllib.request.Request(url, headers={"User-Agent":"curl/8.4.0"})
    try:
        r=urllib.request.urlopen(req,timeout=30); return getattr(r,"status",200), json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: b=e.read().decode("utf-8","replace")[:200]
        except Exception: b=""
        return e.code, b
    except Exception as e: return "ERR:%r"%e, None
def slugdate(s):
    m=re.search(r"(\d{4}-\d{2}-\d{2})", s or ""); return m.group(1) if m else None
def evlist(data): return data if isinstance(data,list) else ((data or {}).get("events") or [])
def show(tag, evs):
    ufc=[e for e in evs if str(e.get("slug","")).startswith("ufc-")]
    print("  [%s] events=%d ufc-*=%d"%(tag, len(evs), len(ufc)))
    for e in ufc[:25]:
        mkts=e.get("markets") or []
        names=[m.get("groupItemTitle") or "" for m in mkts if m.get("groupItemTitle")]
        print("     slug=%-44s date=%s title=%r fighters~=%s"%(e.get("slug"), slugdate(e.get("slug")), (e.get("title") or "")[:34], names[:6]))
    return ufc

print("### strategy 1: date-window Sep 4-10 2026 (the Kalshi 26SEP05 / 26SEP08 cards) ###")
for qs in ("closed=false&start_date_min=2026-09-04T00:00:00Z&start_date_max=2026-09-10T00:00:00Z&limit=300",
           "start_date_min=2026-09-04T00:00:00Z&start_date_max=2026-09-10T00:00:00Z&limit=300"):
    code,data=get(G+"/events?"+qs)
    if isinstance(data,str): print("  qs=%s -> http=%s body=%r"%(qs[:40],code,data)); continue
    show("win:"+qs[:28], evlist(data))

print("### strategy 2: tag_slug=ufc / mma ###")
for tg in ("ufc","mma"):
    code,data=get(G+"/events?tag_slug=%s&closed=false&limit=60"%tg)
    if isinstance(data,str): print("  tag=%s -> http=%s body=%r"%(tg,code,data)); continue
    show("tag:"+tg, evlist(data))

print("### strategy 3: page open events, collect ufc ###")
allufc=[]
for off in (0,100,200,300,400,500):
    code,data=get(G+"/events?closed=false&limit=100&offset=%d&order=startDate&ascending=true"%off)
    if isinstance(data,str): break
    evs=evlist(data)
    allufc += [e for e in evs if str(e.get("slug","")).startswith("ufc-")]
    if len(evs)<100: break
print("  paged ufc-* total=%d"%len(allufc))
for e in allufc[:25]:
    print("     slug=%-44s date=%s title=%r"%(e.get("slug"), slugdate(e.get("slug")), (e.get("title") or "")[:34]))
PY
echo "### DONE ###"
