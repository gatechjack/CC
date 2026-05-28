"""Shared HTTP helpers for the Polymarket weather investigation (read-only).

Pure stdlib (urllib) so it never imports trading_corp — no run_capped needed.
Cloudflare blocks the default urllib UA, so we always send a browser UA.
Retry/backoff on 403(cloudflare)/429/5xx. Modest thread concurrency.
"""
from __future__ import annotations
import json, time, urllib.request, urllib.parse, random
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"


def get_json(url, *, params=None, max_retries=6, timeout=40):
    """GET with browser UA + backoff. Returns parsed JSON or raises."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params, doseq=True)
    last = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            code = e.code
            if code in (403, 429) or 500 <= code < 600:
                delay = min(2.0 * (2 ** attempt), 30.0) * (0.6 + random.random() * 0.6)
                time.sleep(delay)
                continue
            raise
        except Exception as e:  # timeout / conn reset
            last = e
            time.sleep(min(1.5 * (2 ** attempt), 20.0))
            continue
    raise RuntimeError(f"get_json gave up after {max_retries}: {url[:120]} :: {last}")


def paginate(url, *, params=None, page=100, key_limit="limit", key_offset="offset",
             max_pages=200):
    """Generic offset pagination over a list endpoint. Yields rows."""
    params = dict(params or {})
    off = 0
    for _ in range(max_pages):
        params[key_limit] = page
        params[key_offset] = off
        rows = get_json(url, params=params)
        if not isinstance(rows, list) or not rows:
            break
        for r in rows:
            yield r
        if len(rows) < page:
            break
        off += page


def fetch_all(url, *, params=None, page=100, max_pages=200):
    return list(paginate(url, params=params, page=page, max_pages=max_pages))


def paginate_keyset(base_url, *, params=None, max_pages=800):
    """Cursor pagination for gamma /markets/keyset (offset caps at 10100).
    Returns the full list of market dicts. Page size is server-capped ~100."""
    params = dict(params or {})
    cur = None
    seen_cursors = set()
    out = []
    for _ in range(max_pages):
        p = dict(params)
        if cur:
            p["next_cursor"] = cur
        d = get_json(base_url, params=p)
        if not isinstance(d, dict):
            break
        rows = d.get("markets") or []
        out.extend(rows)
        cur = d.get("next_cursor")
        if not rows or not cur or cur in seen_cursors:
            break
        seen_cursors.add(cur)
    return out


def map_concurrent(fn, items, *, workers=5, label=""):
    """Run fn over items with a thread pool; returns list of (item, result_or_None)."""
    out = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for f in as_completed(futs):
            it = futs[f]
            try:
                out.append((it, f.result()))
            except Exception as e:
                out.append((it, None))
            done += 1
            if label and done % 50 == 0:
                print(f"  [{label}] {done}/{len(items)}", flush=True)
    return out


def load(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    print(f"  saved {path} ({len(obj) if hasattr(obj,'__len__') else '?'} items)", flush=True)
