# pk_demote_2whales.ps1 -- OPERATOR-RUN, live-roster change on the ARMED poly_kalshi_mlb division.
# Demotes 2 whales live_whales -> paper selected_whales using the RATIFIED Phase 2a demote
# (roster_split.demote_whale_to_paper: atomic 3-key -live_whales/+selected/+pinned; NO broker action;
# open live positions RIDE TO SETTLEMENT). KEEPS SDTrading + xifutloong3 live.
#
# NO RESTART NEEDED: the live loop (_load_roster) and paper sim (_load_selected_whales) re-read their
# roster keys FRESH each poll cycle, so the move takes effect on the next poll (<=7s live / <=60s paper).
# No re-arm, no flag-3 window.
#
# VERIFY-THEN-MUTATE:
#   powershell -ep bypass -f .\pk_demote_2whales.ps1            # DRY: show current rosters + plan, NO mutation
#   powershell -ep bypass -f .\pk_demote_2whales.ps1 -Apply     # APPLY: atomic demote x2 + read-back assert
#   powershell -ep bypass -f .\pk_demote_2whales.ps1 -Reverse   # UNDO: move the 2 back to live (exact inverse)
param([switch]$Apply, [switch]$Reverse)
$ErrorActionPreference = 'Stop'
if ($Reverse) { $mode = 'REVERSE' } elseif ($Apply) { $mode = 'APPLY' } else { $mode = 'DRY' }
$py = @'
import os
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
MODE = "__MODE__"
from trading_corp.persistence import db
from trading_corp.persistence.db import set_agent_state_multi
from trading_corp.agents.strategies.roster_split import (
    extract_wallets, wallet_of, assert_disjoint, demote_whale_to_paper,
    LIVE_ACTOR, LIVE_KEY, PAPER_ACTOR, PAPER_KEY, PIN_KEY,
)
KEEP = {"0x16bb9951a36fce71e2ef57890b786145e0ba8492": "SDTrading",
        "0x2dc13c6bda81b202281e796953a7323de675b33c": "xifutloong3"}
DEMOTE = {"0x684baa57c338c2549aec0aa3f034f695d72a8409": "monkeymashingkeyboard",
          "0x9c3ce009c9b039956665cecc4cd14de862b5e8c9": "0x0x23kjookhaiuohduoayh8c9"}

def load(a, k):
    r = db.load_agent_state(a, k, db_url=DB)
    return list(r[0]) if r and isinstance(r[0], list) else []

def show(tag):
    l = load(LIVE_ACTOR, LIVE_KEY); s = load(PAPER_ACTOR, PAPER_KEY); p = load(PAPER_ACTOR, PIN_KEY)
    lw, sw, pw = extract_wallets(l), extract_wallets(s), extract_wallets(p)
    print("%s live_whales n=%d %s" % (tag, len(lw), sorted(lw)))
    print("%s selected_whales(paper) n=%d %s" % (tag, len(sw), sorted(sw)))
    print("%s pinned_whales n=%d %s" % (tag, len(pw), sorted(pw)))
    return lw, sw, pw

def open_pos(wallet):
    with db.connect(DB) as c:
        return c.execute("select count(*) from audit_event a left join kalshi_round_trips r on r.order_id=json_extract(a.payload_json,'$.order_id') where a.actor='poly_kalshi_mlb' and a.kind='poly_kalshi_order' and json_extract(a.payload_json,'$.status')='placed' and coalesce(json_extract(a.payload_json,'$.action'),'entry')='entry' and coalesce(json_extract(a.payload_json,'$.order_id'),'')!='' and lower(json_extract(a.payload_json,'$.whale_wallet'))=? and r.order_id is null", (wallet,)).fetchone()[0]

MK = "0x684baa57c338c2549aec0aa3f034f695d72a8409"
KJ = "0x9c3ce009c9b039956665cecc4cd14de862b5e8c9"
print("MODE", MODE)
lw, sw, pw = show("BEFORE")
print("OPEN_POS BEFORE monkeymashingkeyboard=%d 0x0x23kj=%d (ride-to-settlement; demote must NOT change these)" % (open_pos(MK), open_pos(KJ)))

if MODE in ("DRY", "APPLY"):
    missing = [w for w in DEMOTE if w not in lw]
    if missing:
        print("ABORT demote target(s) not in live_whales: %s -- state drift, review" % [m[:12] for m in missing])
        raise SystemExit(2)
    for w in KEEP:
        if w not in lw:
            print("WARN keep-whale %s not currently in live_whales" % KEEP[w])
    print("PLAN demote -> paper: %s | KEEP live: %s" % (sorted(DEMOTE.values()), sorted(KEEP.values())))
    if MODE == "DRY":
        print("DRY no mutation. Re-run with -Apply to demote; -Reverse to undo.")
        raise SystemExit(0)
    for w, name in DEMOTE.items():
        demote_whale_to_paper(w, db_url=DB)   # ratified atomic 3-key; no broker action
    lw2, sw2, pw2 = show("AFTER")
    end_ok = (lw2 == set(KEEP)) and set(DEMOTE).issubset(sw2) and set(DEMOTE).issubset(pw2)
    try:
        assert_disjoint(lw2, sw2); inv = "OK"
    except Exception as e:
        inv = "VIOLATED %s" % e
    print("INVARIANT live-cap-paper == empty : %s" % inv)
    print("OPEN_POS AFTER monkeymashingkeyboard=%d 0x0x23kj=%d (UNCHANGED == ride-to-settlement intact)" % (open_pos(MK), open_pos(KJ)))
    print("DEMOTE_OK", bool(end_ok and inv == "OK"))
    print("REVERSE_HINT re-run with -Reverse to undo (moves the 2 back to live_whales)")
    raise SystemExit(0)

# REVERSE: exact inverse -- move the 2 back to live_whales, remove from selected + pinned. No flatten.
for w, name in DEMOTE.items():
    live = load(LIVE_ACTOR, LIVE_KEY); sel = load(PAPER_ACTOR, PAPER_KEY); pin = load(PAPER_ACTOR, PIN_KEY)
    meta = next((x for x in (sel + pin + live) if isinstance(x, dict) and wallet_of(x) == w), {"wallet": w, "user_name": name})
    live_after = live if w in extract_wallets(live) else live + [meta]
    sel_after = [x for x in sel if wallet_of(x) != w]
    pin_after = [x for x in pin if wallet_of(x) != w]
    set_agent_state_multi([(LIVE_ACTOR, LIVE_KEY, live_after), (PAPER_ACTOR, PAPER_KEY, sel_after), (PAPER_ACTOR, PIN_KEY, pin_after)], db_url=DB)
lw3, sw3, pw3 = show("AFTER")
try:
    assert_disjoint(lw3, sw3); print("INVARIANT OK")
except Exception as e:
    print("INVARIANT VIOLATED %s" % e)
print("REVERSED both back to live_whales")
'@
$py = $py -replace "__MODE__", $mode
$py = $py -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
$sh = "cd /home/azureuser/trading_corp && printf %s '$b64' | base64 -d | runuser -u azureuser -- venv/bin/python3 -"
Write-Host "== Phase 2a DEMOTE 2 whales ($mode): monkeymashingkeyboard + 0x0x23kj live->paper; KEEP SDTrading+xifutloong3 =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
