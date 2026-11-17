"""Record which (series_ticker, status) pairs every ctx builder requests -- READ-ONLY, ZERO network.
Runs ON THE BOX against the pvc_scratch staged tree (venv python). The stub serves EMPTY catalogs and
records the request; an empty fetch is a documented-safe builder state ("off-season/empty -> empty index")."""
import asyncio
import json
import sys
import time
import urllib.parse as up

sys.path.insert(0, "/home/azureuser/pvc_scratch")
from trading_corp.prediction_markets import live_driver as LD  # noqa: E402


class Rec:
    def __init__(self):
        self.pairs = []

    async def get(self, path):
        qs = up.parse_qs(up.urlparse(path).query)
        self.pairs.append(((qs.get("series_ticker") or [""])[0], (qs.get("status") or ["open"])[0]))
        return {"markets": [], "cursor": ""}

    async def get_markets(self, series_ticker=None, status=None, **kw):
        s = str(getattr(status, "value", status) or "open").lower().rsplit(".", 1)[-1]
        self.pairs.append((series_ticker, s))
        return []


async def main():
    rec = Rec()
    errs = {}
    for cat, bld in sorted(LD.CATEGORY_CTX_BUILDERS.items()):
        try:
            await bld(rec, int(time.time()))
        except Exception as e:  # noqa: BLE001 -- report; the pairs recorded so far still count
            errs[cat] = repr(e)[:200]
    print(json.dumps({"pairs": sorted(set(rec.pairs)), "builder_errors": errs}))


asyncio.run(main())
