# pk_cutover_seed.ps1 -- Phase 2a CP5 cutover runner (OPERATOR-RUN at CP6, RIGHT BEFORE restart).
#
# Atomic 3-key roster cutover: seed poly_kalshi_mlb/live_whales = the CURRENT
# polymarket_copy_trader/selected_whales (today == the 4 live-traded whales) AND
# remove them from selected_whales AND pinned_whales -- ONE set_agent_state_multi
# transaction (BEGIN IMMEDIATE...COMMIT). Wallets are read from live state at
# runtime (NOT hardcoded); the DRY run displays them for operator confirmation.
#
# VERIFY-THEN-MUTATE (run DRY first, confirm the 4 wallets, then -Apply):
#   powershell -ep bypass -f .\pk_cutover_seed.ps1            # DRY: read + show the plan, NO mutation
#   powershell -ep bypass -f .\pk_cutover_seed.ps1 -Apply     # APPLY: atomic move + read-back assert
#   powershell -ep bypass -f .\pk_cutover_seed.ps1 -Reverse   # UNDO: move live_whales back to paper
#
# ORDERING (CP6): run -Apply AFTER the CP2+ files are installed (needs the deployed
# roster_split + db.set_agent_state_multi -- there is a preflight guard) and RIGHT
# BEFORE the engine restart (CP3 retargets the live loop to live_whales, which is
# EMPTY until this seeds it). Reversible via -Reverse. Runs as azureuser so the
# DB/journal ownership matches the engine. APPLY aborts unless exactly EXPECT_N=4
# whales are in selected_whales (edit EXPECT_N if the roster legitimately changed).
param([switch]$Apply, [switch]$Reverse)
$ErrorActionPreference = 'Stop'
if ($Reverse) { $mode = 'REVERSE' } elseif ($Apply) { $mode = 'APPLY' } else { $mode = 'DRY' }
$py = @'
import os
try:
    from dotenv import load_dotenv
    load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
MODE = "__MODE__"
EXPECT_N = 4
from trading_corp.persistence import db
# Preflight: the cutover needs the deployed CP2/CP3/CP4 code (install files first).
if not hasattr(db, "set_agent_state_multi"):
    print("ABORT set_agent_state_multi missing -- install the CP2+ files BEFORE the cutover")
    raise SystemExit(3)
from trading_corp.agents.strategies.roster_split import (
    extract_wallets, wallet_of, assert_disjoint,
    LIVE_ACTOR, LIVE_KEY, PAPER_ACTOR, PAPER_KEY, PIN_KEY,
)

def load(actor, key):
    rec = db.load_agent_state(actor, key, db_url=DB)
    return list(rec[0]) if rec and isinstance(rec[0], list) else []

sel = load(PAPER_ACTOR, PAPER_KEY)
pin = load(PAPER_ACTOR, PIN_KEY)
live = load(LIVE_ACTOR, LIVE_KEY)
print("MODE", MODE)
print("BEFORE selected_whales n=%d wallets=%s" % (len(sel), sorted(extract_wallets(sel))))
print("BEFORE pinned_whales   n=%d wallets=%s" % (len(pin), sorted(extract_wallets(pin))))
print("BEFORE live_whales     n=%d wallets=%s" % (len(live), sorted(extract_wallets(live))))

def show_after(tag):
    s = load(PAPER_ACTOR, PAPER_KEY); p = load(PAPER_ACTOR, PIN_KEY); l = load(LIVE_ACTOR, LIVE_KEY)
    print("%s selected_whales n=%d wallets=%s" % (tag, len(s), sorted(extract_wallets(s))))
    print("%s pinned_whales   n=%d wallets=%s" % (tag, len(p), sorted(extract_wallets(p))))
    print("%s live_whales     n=%d wallets=%s" % (tag, len(l), sorted(extract_wallets(l))))
    try:
        assert_disjoint(extract_wallets(l), extract_wallets(s))
        print("%s INVARIANT_OK live-cap-paper == empty" % tag)
        return True
    except Exception as e:
        print("%s INVARIANT_VIOLATED %s" % (tag, e))
        return False

if MODE == "REVERSE":
    # UNDO: move every live_whales entry back to selected + pinned; clear live.
    back = list(live); bw = extract_wallets(live)
    live_after = []
    sel_after = list(sel) + [x for x in back if wallet_of(x) not in extract_wallets(sel)]
    pin_after = list(pin) + [x for x in back if wallet_of(x) not in extract_wallets(pin)]
    print("PLAN REVERSE move %d wallet(s) live->paper: %s" % (len(bw), sorted(bw)))
    db.set_agent_state_multi([
        (LIVE_ACTOR, LIVE_KEY, live_after),
        (PAPER_ACTOR, PAPER_KEY, sel_after),
        (PAPER_ACTOR, PIN_KEY, pin_after),
    ], db_url=DB)
    show_after("AFTER")
    raise SystemExit(0)

# FORWARD (DRY or APPLY): every current selected whale -> live; cleared from selected + pinned.
move = list(sel); mw = extract_wallets(sel)
sel_after = [x for x in sel if wallet_of(x) not in mw]
pin_after = [x for x in pin if wallet_of(x) not in mw]
have = extract_wallets(live)
live_after = list(live) + [x for x in move if wallet_of(x) not in have]
print("PLAN move %d selected whale(s) -> live: %s" % (len(mw), sorted(mw)))
print("PLAN selected_after n=%d pinned_after n=%d live_after n=%d" % (len(sel_after), len(pin_after), len(live_after)))
if len(mw) != EXPECT_N:
    print("WARN expected %d live whales in selected_whales, found %d -- REVIEW before -Apply" % (EXPECT_N, len(mw)))

if MODE == "DRY":
    print("DRY no mutation. Re-run with -Apply to commit; -Reverse to undo.")
    raise SystemExit(0)

# APPLY
if len(mw) != EXPECT_N:
    print("ABORT_APPLY expected %d selected whales, found %d -- no mutation" % (EXPECT_N, len(mw)))
    raise SystemExit(2)
db.set_agent_state_multi([
    (PAPER_ACTOR, PAPER_KEY, sel_after),
    (PAPER_ACTOR, PIN_KEY, pin_after),
    (LIVE_ACTOR, LIVE_KEY, live_after),
], db_url=DB)
ok = show_after("AFTER")
la = load(LIVE_ACTOR, LIVE_KEY); sa = load(PAPER_ACTOR, PAPER_KEY); pa = load(PAPER_ACTOR, PIN_KEY)
seeded = mw.issubset(extract_wallets(la))
cleared = (not (mw & extract_wallets(sa))) and (not (mw & extract_wallets(pa)))
print("CUTOVER_OK", bool(ok and seeded and cleared))
print("REVERSE_HINT re-run this script with -Reverse to undo the cutover")
'@
$py = $py -replace "__MODE__", $mode
$py = $py -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
$sh = "cd /home/azureuser/trading_corp && printf %s '$b64' | base64 -d | runuser -u azureuser -- venv/bin/python3 -"
Write-Host "== Phase 2a CP5 cutover ($mode) : poly_kalshi_mlb/live_whales seed =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
