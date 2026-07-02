# Next-session prompt — paste into a fresh session

---

Directory is cc — work in C:\Users\AA Incorporado\cc. claude --worktree.
Read command-paste-rule and confirm; format all operator-paste commands for PowerShell, ONE line ≤100 chars,
no wrapping. Discipline: delegate to Sonnet when sufficient; stop at forks; surface anomalies with detail; hold
scope; commit artifacts incrementally; nothing pushed/merged without my word.

SSH READ-ONLY for verification (cat / git rev-parse / md5sum / journalctl / sqlite SELECT). NEVER git
stash/clean; never paste/request the prod root password. A concurrent agent may be on other divisions — git
state may move; report, don't react. Prod deploys are targeted-hunk (main DIVERGES from prod). Do NOT merge to
main. Handoff: reports/2026-07-02_session_handoff.md.

CONTEXT (trust over older memory):
- Engine LIVE PID 60341 (boot 2026-07-02 16:23 UTC). Live divisions all paper=False: bitunix_sfp,
  bitunix_futures, robinhood_pead, kalshi_copy_trading.
- bitunix_sfp = bidirectional regime-aware Mode-B, 4 coins armed live, regime=up (long-only), flat. MAIDEN
  SHORT still untested. Futures SL-trail fix DEPLOYED 2026-07-02 (prod reconciler = commit 701a9fb, md5
  25833c1e; main/origin = pre-fix — future main reconcile must carry the hunk).
- Wick-test scalp research RETIRED (v1–v6, no edge) — do NOT re-run (BACKLOG RESEARCH LEDGER).

STEP 1 — re-verify live state read-only (report raw): engine PID (expect 60341) + NRestarts + tracebacks since
boot; SFP flat/armed all 4 coins + regime labels per coin (bitunix_sfp_regime_state); research_log/regime_flip
counts; ANY bitunix_futures OR bitunix_sfp fills since 2026-07-02 16:23. If a fill happened unwatched, run the
first-live A/B checks on it NOW and report.

STEP 2 — pick ONE (ask me if unsure):
(A) **SFP maiden-short first-live A/B watch** (highest value): if/when a SHORT fills, verify TP rests with a
    real /tpsl/ id, OCO closes + B1 auto-cancels with no orphan, auto-book 2R, research-log regime stamp
    correct. Watch the maiden short hardest (slPrice ABOVE entry — venue-unexercised). HOT rollback lever:
    `strategies.yaml bitunix_sfp side: regime → long` + restart.
(B) **Futures SL-trail fix live-validation:** on the next futures bracket close, confirm the new INFO
    "post-close no-op" breadcrumb replaced the old `positionId absent` WARNING (journalctl).
(C) **Futures pre-TP1 trail backtest** (BACKLOG P3): design + backtest whether a pre-TP1 price/ATR trail would
    cut initial-stop losers (2026-07-02 both futures shorts stopped at initial with no trail). GROSS, k=1
    causal, null-gated, own pre-registration. (Note the wick-test arc's lesson: any short "edge" in a bear
    window must clear a drift-embedding null; the long side is the bear-proof tell.)
(D) **Main-reconcile scoping** (merge-debt): plan a rebase-onto-current-prod that carries BOTH the CRLF hybrids
    (main.py/strategies.yaml) AND the SL-trail reconciler hunk (701a9fb) — scope only, no merge.

Deliverable: as specified per task; committed artifacts + report; nothing pushed/merged without my word.
