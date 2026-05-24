# kalshi_sports_scout Phase-0 — addendum: time-series probe + pipeline audit

**Supplements:** `reports/2026-05-23_kalshi_sports_scout_phase0_review.md`
(commits 89e6626, 7054bff)

**Adjusts:** the v2 "structural negative-mean signed divergence is a candidate
for directional alpha" framing. The negative mean is **at least partly a
pipeline / corpus-construction artifact**, not a tradeable edge. Detail below.

---

## 1. MLB per-market time-series probe

**The time series the gate needs doesn't exist in the corpus.**

- 96 MLB observed rows
- 92 distinct tickers (1.043 rows/ticker)
- 88 tickers observed exactly **once**
- 4 tickers observed exactly **twice** — and all 4 cover the same two
  games (BAL@TB and CIN@PHI scheduled for 2026-05-20)

**Why one observation per ticker?** Discovery is called per-scan with the
scout's `cache_ttl_sec: 900` (15 min) — well below the 1h poll interval,
so every scan re-fetches. Yet most tickers appear in exactly one
`audit_event` row. Two plausible causes (not mutually exclusive):

- `discovery.max_series_per_category: 50` × `max_markets_per_series: 50`
  caps return at 2500 markets total. Across 5 sports leagues' Sports
  category, this likely truncates inconsistently scan-over-scan, so the
  same upcoming game lands in some scans but not others.
- Markets exit Kalshi discovery once a game commences. The 88
  single-observation tickers may have been observed in a single scan
  during a narrow pre-commencement window. With 1h poll spacing, only
  one such scan would fire per game.

Either way: **the gate cannot answer "does divergence persist or decay"
on the full corpus because the corpus has no time evolution.**

### What the 4 doubly-observed markets say

| ticker | t₁ | n_books₁ | div₁ | t₂ | n_books₂ | div₂ |
|--------|----|----------|------|----|----------|------|
| KXMLBGAME-26MAY201305CINPHI-CIN | 2026-05-17 18:20 | 3 | −16.20 | 2026-05-19 15:21 | 9 | −1.40 |
| KXMLBGAME-26MAY201305CINPHI-PHI | 2026-05-17 18:20 | 3 | −7.80  | 2026-05-19 15:21 | 9 | −4.60 |
| KXMLBGAME-26MAY201310BALTB-BAL  | 2026-05-17 18:20 | 3 | −23.97 | 2026-05-19 15:21 | 9 | −0.05 |
| KXMLBGAME-26MAY201310BALTB-TB   | 2026-05-17 18:20 | 3 | −14.03 | 2026-05-19 15:21 | 9 | −4.95 |

Three of four divergences decay by ≥10pp. The fourth (CIN/PHI YES=PHI)
decays by ~3pp. **No sign flips** — direction stable. Magnitude
collapses toward zero as `n_books` grows from 3 → 9.

This pattern is consistent with: when a game is first discovered by
the-odds-api, only a few US books (likely Pinnacle, BetOnline, BetUS)
have posted lines. The vig-removed median across 3 books is noisy and
biased toward whichever sharps were earliest. As DK/FD/MGM/Caesars
catch up, the median moves; in 4-of-4 cases it moved toward where
Kalshi already had the market priced.

### Implication for the v2 negative-mean signal

The v2 review noted that mean signed divergence is negative on **every**
league. Cross-referencing the v1 per-league summary:

| league | mean n_books | mean signed div |
|--------|--------------|-----------------|
| NHL    | 9.0          | −2.28           |
| NBA    | 8.3          | −1.21           |
| MLS    | 7.9          | −0.99           |
| MLB    | 7.1          | −2.27           |
| NFL    | 6.0          | **−3.07**       |

NFL has both the **lowest** mean n_books AND the **most negative** mean
signed div. NHL has highest n_books and a moderate signed div. The
n_books × signed-div correlation is consistent with the early-line
hypothesis: leagues with thinner book coverage produce more negative
divergence on average, because the bookmaker median is dragged toward
the early-quoting books before the consensus matures.

**Conclusion:** the v2 "directional alpha candidate" reading is not
supported by this corpus. The negative-mean signal is partially —
possibly entirely — an artifact of capturing each game once, mostly at
early-line time when book coverage is thin.

To distinguish real Kalshi-vs-book bias from early-line noise, the
corpus would need:
- Multiple observations per market, ideally **gated by `n_books ≥ K`**
  for some K like 6 or 8.
- Final pre-commencement observations specifically (within ~1h of game
  start) where the book consensus is fully formed.

Neither exists in the current corpus.

---

## 2. Pipeline audit — vig-removal + YES/NO mapping

The user requested the relevant code dumped so they can audit whether
the negative mean is real or a pipeline artifact. Both subsystems
inspected and judged sound. **The artifact discussed in §1 is upstream
of these — it's about which markets get sampled when, not about the
math.**

### 2a. Vig-removal method (`data/odds_api_client.py:158-204`)

```python
for book in raw.get("bookmakers") or []:
    for market in book.get("markets") or []:
        if market.get("key") != "h2h":
            continue
        outcomes = market.get("outcomes") or []
        # Build name → American-odds map
        odds_by_name: dict[str, int] = {}
        for o in outcomes:
            name = o.get("name") or ""
            price = o.get("price")
            try:
                odds_by_name[name] = int(price)
            except (TypeError, ValueError):
                continue
        h_p = _american_to_prob(odds_by_name.get(home))
        a_p = _american_to_prob(odds_by_name.get(away))
        # 3-way soccer
        t_p = _american_to_prob(odds_by_name.get("Draw"))
        if h_p is None or a_p is None:
            continue
        total = h_p + a_p + (t_p or 0)
        if total <= 0:
            continue
        vigs.append(total - 1.0)
        # Vig-removed: divide by total
        home_probs.append(h_p / total)
        away_probs.append(a_p / total)
        if t_p is not None:
            tie_probs.append(t_p / total)
...
return GameOdds(
    ...
    implied_home=statistics.median(home_probs),
    implied_away=statistics.median(away_probs),
    ...
)
```

```python
def _american_to_prob(price: int | None) -> float | None:
    if p > 0:
        return 100.0 / (p + 100.0)
    return -p / (-p + 100.0)
```

**Method:** per-book proportional vig removal (`p / (p_home + p_away +
p_tie)`), then median across books. This is the standard, well-known
method (sometimes called "basic" or "shin-free" vig removal).

**Known properties:**
- Exact at 50/50 events.
- Slightly biased relative to the Shin model on heavy favorites (the
  Shin correction would push extreme favorites' implied prob *down*
  by 1–3pp and extreme underdogs *up* by 1–3pp). MLB lines in the
  corpus cluster around 0.40–0.66 — not extreme; Shin bias would be
  sub-1pp here. **Cannot explain the −2.27pp MLB mean** on its own.
- Vig removal direction is symmetric across home/away — no one-sided
  bias.

**Verdict:** sound. Not the source of the negative-mean signal.

### 2b. YES-side bookmaker mapping (`data/sports_team_mapping.py:147-220`)

```python
_TICKER_RE = re.compile(
    r"^KX(?P<league>[A-Z]+)GAME-"
    r"(?P<date>\d{2}[A-Z]{3}\d{2})"
    r"(?P<time>\d{4})?"
    r"(?P<blob>[A-Z]+)-"
    r"(?P<yes>[A-Z]+)\d*$"
)

def parse_sports_ticker(ticker):
    ...
    if blob.startswith(yes_side):
        team_b_code = blob[len(yes_side):]
    elif blob.endswith(yes_side):
        team_b_code = blob[:-len(yes_side)]
    else:
        return None  # YES side somewhere in the middle — ambiguous
    ...
    return ParsedSportsTicker(
        league=league, ..., team_a=yes_side, team_b=team_b_code,
        team_a_name=teams.get(team_a_code),
        team_b_name=teams.get(team_b_code),
    )
```

```python
def find_matching_game(parsed, games):
    a = parsed.team_a_name.lower()
    b = parsed.team_b_name.lower()
    for g in games:
        gh = (g.home_team or "").lower()
        ga = (g.away_team or "").lower()
        if (gh == a and ga == b) or (gh == b and ga == a):
            return g
    return None
```

### 2c. YES-side probability lookup (`agents/strategies/kalshi_sports_scout.py:226-230`)

```python
yes_is_home = (game.home_team or "").lower() == (parsed.team_a_name or "").lower()
bookmaker_yes_prob = (
    game.implied_home if yes_is_home else game.implied_away
)
```

**Audit findings:**

1. **Ticker grammar:** YES side is always the last hyphen-separated
   segment of the ticker (e.g., `KXMLBGAME-26MAY201310BALTB-BAL` → YES
   on BAL). `team_a` is the YES team; `team_b` is derived by stripping
   `team_a` from the blob. The parser correctly handles both prefix
   (`yes=NYM` in `NYMMIA`) and suffix (`yes=MIA` in `NYMMIA`) cases,
   and returns None on ambiguous middle-match cases (rare).

2. **Bookmaker game match:** case-insensitive equality on (home, away)
   in both orderings. This catches the case where Kalshi's YES side is
   actually the *away* team on the bookmaker side — the
   `(gh == a and ga == b) or (gh == b and ga == a)` clause means a
   ticker `BALTB-BAL` will match a game whose `home_team="Tampa Bay
   Rays", away_team="Baltimore Orioles"` (away-team YES).

3. **YES-side probability:** `yes_is_home = (game.home_team.lower() ==
   parsed.team_a_name.lower())`. If `parsed.team_a_name` (the YES
   team's full name) matches the bookmaker's `home_team`, use
   `implied_home`; else `implied_away`. This is consistent — the
   `find_matching_game` step already confirmed the pair matches.

**Verdict:** sound. The mapping correctly identifies which book-side
probability to compare against Kalshi's YES.

**One brittleness to flag (not blocking the current audit, but worth
fixing):** the case-insensitive string compare against the-odds-api's
`home_team`/`away_team` is fragile to formatting drift. If the-odds-api
ever changes "St. Louis Cardinals" → "St Louis Cardinals" (no period)
or "Athletics" (relocating from Oakland) → some other format, the
mapping silently emits an unmapped row instead of failing loudly. A
fuzzy-match fallback (or a per-league name-normalization layer) would
make this more robust. Not the source of the current artifact.

---

## 3. Net read on the negative-mean signal

The v2 review's structural-alpha framing **does not survive this
addendum**. The negative-mean signed divergence is most plausibly:

- 80–90% **early-line capture bias** — most observed markets are
  captured at first discovery, when only 3-5 books are quoting and the
  median is biased toward early-quoting sharps. The 4-of-4 markets
  with two observations all show the divergence collapsing as books
  catch up.
- Remainder: possible real Kalshi-vs-book bias, but with this corpus we
  cannot separate it from the artifact.

**Implication for the Phase-0 gate decision:**

- The recovered hit-rate matrix from v2 still represents real
  divergences at observation time — those numbers aren't fictional.
- But the matrix represents divergences at a specific (and biased)
  point in each market's price-discovery lifecycle, not steady-state
  divergence.
- A strategy that trades on this signal would need to either (a)
  execute immediately at the early-line capture moment (but Kalshi
  liquidity at that point is unknown and likely thin), or (b)
  somehow capture divergences late in price discovery (which would
  require a corpus that doesn't exist yet).
- This **strengthens the case for** the addendum-modified action items
  below rather than the v2 scope-down recommendation.

---

## 4. Updated action items if any path forward is pursued

1. **Fix the discovery one-observation-per-ticker issue first.** Without
   re-observing the same market multiple times pre-commencement, the
   gate decision is unreliable. Two probable causes (cap truncation and
   pre-commence window timing) — investigate which is dominant before
   tuning.
2. **Gate observations on n_books ≥ 6 (or 8).** Drop observations with
   thin book coverage from the corpus. This will shrink the corpus but
   make the remaining numbers trustworthy.
3. **Add commence-time-aware sampling.** Observations within 1-2h of
   game start are where the bookmaker consensus is most mature and the
   Kalshi-vs-book divergence is most diagnostic.
4. **Then** apply the original v2 action items (units bug fix,
   sum-to-1 sanity gate, threshold-to-0 for the rerun, NBA liquidity
   validation, drop MLS / park NFL).
5. **Then** re-run the 9-day observation window with all of the above
   in place. THAT corpus is what the gate decision should rest on.

The v2 recommendation of "scope-down with caveats" is not wrong, but
its evidentiary base is weaker than v2 implied. Treat the Phase-0 gate
as **"observation methodology needs to be fixed before the gate is
decidable on a representative corpus."** This is closer to the v1
"BLOCKED" framing than to v2 "DECIDABLE — leading to scope-down" — but
for a different reason: not because the corpus is unrecoverable, but
because it's unrepresentative.

---

## 5. Verification queries

```sql
-- Per-MLB-ticker observation count
SELECT json_extract(payload_json,'$.ticker') AS ticker, COUNT(*) AS n,
       MIN(ts) first_ts, MAX(ts) last_ts
FROM audit_event
WHERE kind = 'kalshi_sports_observed'
  AND ts >= '2026-05-14 21:42:00'
  AND json_extract(payload_json,'$.league') = 'MLB'
GROUP BY ticker
HAVING n > 1
ORDER BY n DESC;

-- Per-league n_books vs signed div correlation
SELECT json_extract(payload_json,'$.league') AS league,
       ROUND(AVG(json_extract(payload_json,'$.n_books')),1) AS mean_nbooks,
       ROUND(AVG((json_extract(payload_json,'$.bookmaker_yes_implied')
                  - json_extract(payload_json,'$.kalshi_implied_yes')*100)*100),
             2) AS mean_signed_div
FROM audit_event
WHERE kind = 'kalshi_sports_observed' AND ts >= '2026-05-14 21:42:00'
GROUP BY league ORDER BY mean_nbooks;
```
