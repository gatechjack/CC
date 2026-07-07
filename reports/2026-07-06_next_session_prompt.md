# Next-Session Startup Prompt — after 2026-07-06 SFP arc

## Startup
- Work in `C:\Users\AA Incorporado\cc`. **`claude --worktree`** (worktree isolation per session).
- Read `command-paste-rule` + `working-discipline` memory first; confirm. **All operator-paste commands: one
  line ≤~100 chars, no continuations; .ps1 pure-ASCII, STDIN-stream to ssh, parse-validate.**
- **Read-only SSH is standing practice** (SELECT `-readonly`, journalctl, systemctl status, file reads).
  Writes/restarts stay operator-gated unless explicit per-session Board delegation. **No push, no merge to
  main without the operator's word.** No memory writes unless asked / at wrap.
- Delegate to Sonnet when sufficient; stop-and-report at forks; surface anomalies with diagnostics; hold scope.

## Ground truth (session end 2026-07-07 04:45 UTC — verify before acting)
- Engine **PID 81690**, NRestarts=0, active, boot 2026-07-06 03:28:07 UTC, 0 tracebacks (~25h). *(NOT 60341 —
  that was pre-bracket-deploy.)*
- **origin/main = `a866377`** (bracket min-leg fix merged; deploy_log has it). Prod = main for the bracket file.
- SFP flat all 4 coins (last order 06-28). Futures active. Both reconcilers clean.
- **Regime seed: all 4 coins RANGE** as of 07-07 (flipped from up-bias) → SFP now permits both sides.
- SFP mechanism + gate + pivot(50) **certified** (memory `bitunix-sfp-mechanism-certified-2026-07-06`); the
  regime-gate doubt is RESOLVED (vindicated). Findings are **bear-window-conditioned**.
- Unmerged branches: `sfp-missed-setup-forensic-2026-07-06` @5aa2937 (forensic+spikes),
  `session-wrap-2026-07-06` (this handoff), plus older unmerged (bidirectional b849964 = merge debt).

## Menu (operator picks; nothing is queued/forced)
- **(A) SFP live-behavior check** — since 07-07 the regime flipped to RANGE (both sides allowed). Check for any
  SFP fires since 2026-07-06; if a live fill occurred, first-fill A/B round-trip validation. *(Highest-value —
  the regime flip is the forward event we've been waiting for.)*
- **(B) Bucket-B pattern characterization spike** — what strategy ARE the operator's missed swings (breakout /
  HTF-level / order-block)? **NEW research arc, its own division candidate — NOT an SFP tuning.** Define →
  detect → drift-null → size.
- **(C) Short-watch transition persistence** — small observer-only additive audit-gap fix (mirror long-watch
  persistence; un-reflect levels, side-token watch_id). Closes the ETH-7/1-class blind spot.
- **(D) Futures SL-trail INFO breadcrumb** passive validation.
- **(E) Main-reconcile scoping** — the 07-02 SFP deploy CRLF merge debt (main.py + strategies.yaml, branch
  b849964). Deliberate rebase-onto-prod.
- **(F) Anything the operator brings.**

## Guardrails
- The SFP forensic/pivot/gate arc is **CLOSED** — do not reopen it or start new spike ideas beyond this menu
  unless the operator asks.
- Any bear-window (or trending-window) short-side edge claim MUST pass the drift-embedding null
  (`drift-embedding-null-standard-tool`) — skipping it is untrustworthy.
