"""Manual test harness for the Lord Otter webhook.

Runs from anywhere on the box — no shell-quoting drama. Reads the
webhook secret from .env so it always matches the running server.

Usage:
    python scripts/test_lord_otter_webhook.py                # full Premium chain
    python scripts/test_lord_otter_webhook.py otter_buy      # single signal
    python scripts/test_lord_otter_webhook.py --chain solo   # solo otter (bias-only context)
    python scripts/test_lord_otter_webhook.py --url https://abc.ngrok-free.app  # remote target

What it does:
  - Loads LORD_OTTER_WEBHOOK_SECRET from .env
  - Sends one or more synthetic alerts to the running webhook
  - Pretty-prints each response
  - On the "premium" chain, fires the full sequence:
      bias_bull → pink_box_bull → cvd_bull_flip → otter_buy
    so you should see the Otter Buy come back with status=would_have_placed,
    tier=premium.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import request, error


def load_secret() -> str:
    """Read LORD_OTTER_WEBHOOK_SECRET from .env (avoids copy-paste mistakes)."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        sys.exit(f"ERROR: .env not found at {env_path}")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("LORD_OTTER_WEBHOOK_SECRET="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if not value:
                sys.exit("ERROR: LORD_OTTER_WEBHOOK_SECRET is empty in .env")
            return value
    sys.exit("ERROR: LORD_OTTER_WEBHOOK_SECRET not found in .env")


def post(url: str, payload: dict) -> tuple[int, dict]:
    """POST JSON, return (status_code, parsed_response).

    Includes `ngrok-skip-browser-warning` header — ngrok free tier
    intercepts requests that look like browser navigations and serves
    a warning page. Sending this header (any value) opts out of that
    behavior. Has no effect when not going through ngrok.
    """
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true",
            # Setting a non-default UA helps too — ngrok's heuristics
            # consider Python-urllib browser-like enough to occasionally
            # serve the warning page.
            "User-Agent": "trading-corp-webhook-test/1.0",
        },
        method="POST",
    )
    try:
        resp = request.urlopen(req, timeout=10)
        body_text = resp.read().decode("utf-8") or "{}"
        try:
            return resp.status, json.loads(body_text)
        except json.JSONDecodeError:
            # Got HTML (e.g. ngrok warning page) instead of JSON — pass
            # the first 200 chars through so the user can see what
            # actually came back.
            return resp.status, {"raw_html_or_text": body_text[:200]}
    except error.HTTPError as e:
        try:
            body_text = e.read().decode("utf-8")
            try:
                data = json.loads(body_text)
            except json.JSONDecodeError:
                data = {"raw_html_or_text": body_text[:200]}
        except Exception:
            data = {"raw": "(unparseable)"}
        return e.code, data
    except error.URLError as e:
        return 0, {"error": f"connection failed: {e.reason}"}


def make_payload(secret: str, signal: str, price: float = 76500.0) -> dict:
    return {
        "secret": secret,
        "signal": signal,
        "ticker": "BTCUSD",
        "exchange": "COINBASE",
        "price": price,
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval": "3",
    }


def fire_one(url: str, secret: str, signal: str) -> None:
    payload = make_payload(secret, signal)
    print(f"\n→ POST {signal}")
    code, data = post(url, payload)
    status = data.get("status", "?")
    print(f"  HTTP {code}  status={status}")
    # Show non-standard fields so we can see what 4xx/5xx responses
    # actually contain (ngrok HTML, server errors, etc.).
    for k in ("decision", "reason", "order_id", "qty", "side", "symbol", "fill_price", "venue", "raw_html_or_text", "error"):
        if k in data:
            v = data[k]
            if isinstance(v, str) and len(v) > 200:
                v = v[:200] + "…"
            print(f"  {k}: {v}")


CHAINS = {
    "premium": ["bias_bull", "pink_box_bull", "cvd_bull_flip", "otter_buy"],
    "diamond": [
        "bias_bull", "ribbon_exhaustion_bull", "pink_box_bull",
        "cvd_bull_flip", "money_bag_bottom", "otter_buy",
    ],
    "standard": ["bias_bull", "cvd_bull_flip", "otter_buy"],
    "solo":     ["bias_bull", "otter_buy"],
    "water":    ["bias_bull", "water_buy_large"],
    "bear":     ["bias_bear", "otter_sell"],   # should be ignored in long-only mode
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "signal", nargs="?", default=None,
        help="Single signal to send. If omitted, runs the --chain flow.",
    )
    parser.add_argument(
        "--chain", default="premium",
        choices=list(CHAINS.keys()),
        help="Pre-defined sequence to fire (default: premium)",
    )
    parser.add_argument(
        "--url", default="http://localhost:8000/webhook/tradingview/lord-otter",
        help="Webhook URL (use your ngrok URL when testing remotely)",
    )
    parser.add_argument(
        "--gap", type=float, default=0.6,
        help="Seconds between signals in a chain (default 0.6)",
    )
    args = parser.parse_args()

    secret = load_secret()
    print(f"target: {args.url}")
    print(f"secret: {secret[:8]}…{secret[-4:]} (loaded from .env)")

    if args.signal:
        fire_one(args.url, secret, args.signal)
    else:
        seq = CHAINS[args.chain]
        print(f"firing chain '{args.chain}': {' → '.join(seq)}")
        for s in seq:
            fire_one(args.url, secret, s)
            time.sleep(args.gap)


if __name__ == "__main__":
    main()
