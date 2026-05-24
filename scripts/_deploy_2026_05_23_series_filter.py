"""One-off deploy script: apply series_filter changes surgically to prod.

NOT for general use. Tagged backup at <path>.pre-series-filter-20260523.
EOL-aware: auto-detects CRLF vs LF per file and matches accordingly.
"""
import hashlib, sys, shutil

TAG = "pre-series-filter-20260523"
BASE = "/home/azureuser/trading_corp"

EDITS = [
    {
        "path": f"{BASE}/trading_corp/data/kalshi_market_map.py",
        "edits": [
            (
                "    max_series_per_category: int = 50,\n"
                "    max_markets_per_series: int = 50,\n"
                "    inter_call_delay_sec: float = 0.15,\n"
                ") -> DiscoveryResult:",
                "    max_series_per_category: int = 50,\n"
                "    max_markets_per_series: int = 50,\n"
                "    series_filter: tuple[str, ...] | frozenset[str] | None = None,\n"
                "    inter_call_delay_sec: float = 0.15,\n"
                ") -> DiscoveryResult:",
            ),
            (
                "    n_categories) get_markets calls + O(events) get_event calls.\n"
                "\n"
                "    Two cost guards",
                "    n_categories) get_markets calls + O(events) get_event calls.\n"
                "\n"
                "    `series_filter` (optional) constrains the iteration to an exact-match\n"
                "    set of series tickers within the requested categories. Out-of-set\n"
                "    series are skipped BEFORE they consume a cap slot - so the\n"
                "    `max_series_per_category` cap counts only in-scope series. This is\n"
                "    how targeted callers (e.g. kalshi_sports_scout) avoid being rotated\n"
                "    out of the returned slice by the much larger catalog of out-of-scope\n"
                "    series sharing the same category. Exact-set semantics (not prefix)\n"
                "    so adjacent series like KXNBAGAMES / KXNBAGAME7 don't sweep in\n"
                "    alongside KXNBAGAME.\n"
                "\n"
                "    Two cost guards",
            ),
            (
                "    import asyncio\n"
                "    from pykalshi import MarketStatus\n"
                "\n"
                "    # Step 1: enumerate series in the target categories.",
                "    import asyncio\n"
                "    from pykalshi import MarketStatus\n"
                "\n"
                "    series_filter_set: frozenset[str] | None = (\n"
                "        frozenset(series_filter) if series_filter is not None else None\n"
                "    )\n"
                "\n"
                "    # Step 1: enumerate series in the target categories.",
            ),
            (
                "            if cat_count >= max_series_per_category:\n"
                "                break\n"
                "            t = getattr(s_obj, \"ticker\", None)\n"
                "            if t:\n"
                "                all_series_tickers.append(t)\n"
                "                cat_count += 1",
                "            if cat_count >= max_series_per_category:\n"
                "                break\n"
                "            t = getattr(s_obj, \"ticker\", None)\n"
                "            if not t:\n"
                "                continue\n"
                "            if series_filter_set is not None and t not in series_filter_set:\n"
                "                continue\n"
                "            all_series_tickers.append(t)\n"
                "            cat_count += 1",
            ),
        ],
    },
    {
        "path": f"{BASE}/trading_corp/brokers/kalshi.py",
        "edits": [
            (
                "        categories: tuple[str, ...] | None = None,\n"
                "        max_series_per_category: int = 30,\n"
                "        max_markets_per_series: int = 50,\n"
                "    ):\n"
                "        \"\"\"Discovery: category -> series -> markets, classified by structural type.",
                "        categories: tuple[str, ...] | None = None,\n"
                "        max_series_per_category: int = 30,\n"
                "        max_markets_per_series: int = 50,\n"
                "        series_filter: tuple[str, ...] | frozenset[str] | None = None,\n"
                "    ):\n"
                "        \"\"\"Discovery: category -> series -> markets, classified by structural type.",
            ),
            (
                "        pattern. Strategies don't talk to pykalshi directly.\n"
                "\n"
                "        Empty result in stub mode (no credentials).",
                "        pattern. Strategies don't talk to pykalshi directly.\n"
                "\n"
                "        `series_filter` constrains discovery to an exact-match set of\n"
                "        series tickers within the requested category(ies). See\n"
                "        `discover_by_categories` for the rationale.\n"
                "\n"
                "        Empty result in stub mode (no credentials).",
            ),
            (
                "            max_series_per_category=max_series_per_category,\n"
                "            max_markets_per_series=max_markets_per_series,\n"
                "        )",
                "            max_series_per_category=max_series_per_category,\n"
                "            max_markets_per_series=max_markets_per_series,\n"
                "            series_filter=series_filter,\n"
                "        )",
            ),
        ],
    },
    {
        "path": f"{BASE}/trading_corp/agents/strategies/kalshi_sports_scout.py",
        "edits": [
            (
                "log = logging.getLogger(__name__)\n"
                "\n"
                "\n"
                "class KalshiSportsScoutAgent:",
                "log = logging.getLogger(__name__)\n"
                "\n"
                "\n"
                "# Game-moneyline series tickers within Kalshi's \"Sports\" category. The\n"
                "# Sports category contains ~2000 series across 50+ market types\n"
                "# (futures, season wins, playoff brackets, props, props variants per\n"
                "# league); we want only the four game-h2h series. Exact-match\n"
                "# semantics (not prefix) so adjacent series like KXNBAGAMES /\n"
                "# KXNBAGAME7 don't sweep in. NFL game-moneyline series does NOT\n"
                "# currently exist in Sports (probe 2026-05-23 found only KXNFLGAMETD /\n"
                "# KXNFLGAMEFG / KXNFLGAMESACK - all props, no game-h2h); re-probe and\n"
                "# re-add once in-season approaches kick-off.\n"
                "_SCOUT_SERIES_FILTER: tuple[str, ...] = (\n"
                "    \"KXMLBGAME\",\n"
                "    \"KXNBAGAME\",\n"
                "    \"KXNHLGAME\",\n"
                "    \"KXMLSGAME\",\n"
                ")\n"
                "\n"
                "\n"
                "class KalshiSportsScoutAgent:",
            ),
            (
                "                self._discovery_cache = await kalshi_broker.list_markets(\n"
                "                    categories=(\"Sports\",),\n"
                "                    max_series_per_category=max_series,\n"
                "                    max_markets_per_series=max_markets,\n"
                "                )",
                "                self._discovery_cache = await kalshi_broker.list_markets(\n"
                "                    categories=(\"Sports\",),\n"
                "                    max_series_per_category=max_series,\n"
                "                    max_markets_per_series=max_markets,\n"
                "                    series_filter=_SCOUT_SERIES_FILTER,\n"
                "                )",
            ),
        ],
    },
    {
        "path": f"{BASE}/config/strategies.yaml",
        "edits": [
            (
                "  discovery:\n"
                "    max_series_per_category: 50\n"
                "    max_markets_per_series: 50\n"
                "    cache_ttl_sec: 900\n"
                "  leagues: [MLB, NBA, NHL, MLS, NFL]",
                "  discovery:\n"
                "    # 2026-05-23: bumped 50 -> 100 with introduction of series_filter in\n"
                "    # discover_by_categories. The cap now counts only in-scope series\n"
                "    # (exact-set match on KXMLBGAME/KXNBAGAME/KXNHLGAME/KXMLSGAME - see\n"
                "    # _SCOUT_SERIES_FILTER in kalshi_sports_scout.py), so 100 is\n"
                "    # comfortably above the 4 in-scope series. Was binding hard before:\n"
                "    # Sports category has ~2000 series; the old 50-cap returned a\n"
                "    # rotating 2.5% slice and in-scope leagues landed only ~11% of scans.\n"
                "    max_series_per_category: 100\n"
                "    max_markets_per_series: 50\n"
                "    cache_ttl_sec: 900\n"
                "  # NFL excluded 2026-05-23: probe found no KXNFLGAME moneyline series\n"
                "  # in Sports (only props: KXNFLGAMETD/FG/SACK). Re-add as game-moneyline\n"
                "  # once that series is actually present - re-probe ~3-4 weeks before\n"
                "  # kick-off and check Sports + Football categories for the right ticker.\n"
                "  leagues: [MLB, NBA, NHL, MLS]",
            ),
        ],
    },
]


def main():
    for spec in EDITS:
        path = spec["path"]
        with open(path, "rb") as f:
            data = f.read()
        is_crlf = b"\r\n" in data[:8192]
        text = data.decode("utf-8")
        md5_before = hashlib.md5(data).hexdigest()
        shutil.copy2(path, f"{path}.{TAG}")
        for i, (old, new) in enumerate(spec["edits"]):
            if is_crlf:
                old_match = old.replace("\n", "\r\n")
                new_match = new.replace("\n", "\r\n")
            else:
                old_match, new_match = old, new
            n = text.count(old_match)
            if n != 1:
                print(f"FAIL: {path} edit #{i+1}: old found {n}x (need 1) - abort")
                sys.exit(1)
            text = text.replace(old_match, new_match, 1)
        new_data = text.encode("utf-8")
        md5_after = hashlib.md5(new_data).hexdigest()
        with open(path, "wb") as f:
            f.write(new_data)
        print(f"OK: {path} crlf={is_crlf} {md5_before} -> {md5_after}")
    print("ALL EDITS APPLIED")


if __name__ == "__main__":
    main()
