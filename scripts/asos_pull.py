"""Pull intraday ASOS hourly temps from IEM for the daily-HIGH nowcast avenue.

For each registry station, fetch hourly tmpf 2021-2026 and reduce to, per UTC date,
the observed temperature at decision hours 14/15/16/17Z (nearest obs at-or-before the
hour, within 75 min). For US stations 14-17Z is local morning on the same calendar
date, so UTC date == local climate date for these snapshots.

ASOS is used here ONLY as a leak-safe forecast FEATURE (obs <= decision hour). NWS CLI
remains the settlement truth (constraint honored). Output one JSONL row per (station,date).
Resumable: skips stations already in the output.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime

OUT = "tmp/asos_decision_temps.jsonl"
HOURS = [14, 15, 16, 17]
YEAR1, YEAR2 = 2021, 2026


def stations_from_yaml():
    raw = open("config/weather_stations.yaml", encoding="utf-8").read()
    block = raw[raw.find("stations:"):raw.find("\nseries:")]
    icaos = re.findall(r"^  (K[A-Z]{3}):\s*$", block, re.M)
    # ASOS code = ICAO without leading K
    return {ic: ic[1:] for ic in icaos}


def fetch(asos_code):
    url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
           f"station={asos_code}&data=tmpf&year1={YEAR1}&month1=1&day1=1&"
           f"year2={YEAR2}&month2=12&day2=31&tz=Etc/UTC&format=onlycomma&"
           "latlon=no&missing=empty&trace=empty")
    for attempt in range(4):
        try:
            return urllib.request.urlopen(url, timeout=180).read().decode("utf-8", "replace")
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def reduce_station(icao, raw):
    # rows: station,valid(YYYY-MM-DD HH:MM),tmpf  -> per (date) nearest obs <= each hour
    # store best (smallest minutes-before-hour, within 75 min) temp per (date,hour)
    best = defaultdict(dict)  # date -> {hour: (delta_min, tmpf)}
    for line in raw.splitlines()[1:]:
        p = line.split(",")
        if len(p) < 3 or not p[2]:
            continue
        try:
            dt = datetime.strptime(p[1], "%Y-%m-%d %H:%M")
            t = float(p[2])
        except (ValueError, IndexError):
            continue
        date = p[1][:10]
        mins = dt.hour * 60 + dt.minute
        for H in HOURS:
            target = H * 60
            delta = target - mins          # obs at-or-before the hour only (no leak)
            if 0 <= delta <= 75:
                cur = best[date].get(H)
                if cur is None or delta < cur[0]:
                    best[date][H] = (delta, t)
    rows = []
    for date, hh in best.items():
        rec = {"icao": icao, "date": date}
        for H in HOURS:
            rec[f"t{H}"] = round(hh[H][1], 1) if H in hh else None
        rows.append(rec)
    return rows


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    done = set()
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            try:
                done.add(json.loads(l)["icao"])
            except Exception:
                pass
    smap = stations_from_yaml()
    todo = [ic for ic in smap if ic not in done]
    if limit:
        todo = todo[:limit]
    print(f"[asos] {len(smap)} stations; {len(done)} done; pulling {len(todo)} -> {OUT}")
    fout = open(OUT, "a", encoding="utf-8")
    try:
        for i, icao in enumerate(todo):
            t0 = time.time()
            raw = fetch(smap[icao])
            rows = reduce_station(icao, raw)
            for r in rows:
                fout.write(json.dumps(r) + "\n")
            fout.flush()
            print(f"[{i+1}/{len(todo)}] {icao}({smap[icao]}): {len(rows)} date-rows "
                  f"in {time.time()-t0:.1f}s")
    finally:
        fout.close()
    print("[asos] done")


if __name__ == "__main__":
    main()
