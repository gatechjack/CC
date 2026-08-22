# pk_pm_bc_run.ps1 -- ONE read-only round trip (Tasks B+C):
#   B: run tests/prediction_markets/ in an ISOLATED ~/pm_p1_scratch (nothing lands in
#      /home/azureuser/trading_corp), with the box venv + PM_DB_PATH=tmp, and PROVE the legacy
#      trading_corp.db is byte-identical (md5+mtime) before and after.
#   C: a read-only gamma /markets tags-schema probe over sample cids spanning the 4 live
#      categories + 2 unknown-tail rows (drawn live from real /closed-positions).
# The box is not a git repo (and no confirmed git creds for the private origin), so instead of a
# git clone this base64-ships the branch's 5 new files into the scratch and COPIES the box's REAL
# conftest/pyproject/trading_corp __init__ (read-only) -- same isolation intent, no network clone.
# NO engine restart, NO existing-file edits, NO sudo, NO creds, NOT a deploy. Cleans up at the end.
# Run: powershell -ep bypass -f .\pk_pm_bc_run.ps1
$ErrorActionPreference = 'Stop'
$root = "C:\Users\AA Incorporado\cc-prediction-markets-wt"
$files = @(
    'trading_corp/prediction_markets/__init__.py',
    'trading_corp/prediction_markets/db.py',
    'trading_corp/prediction_markets/category.py',
    'tests/prediction_markets/test_db.py',
    'tests/prediction_markets/test_category.py'
)

# ---- build the file-injection bash (base64 each committed file -> heredoc decode into $S) ----
$inject = ""
for ($k = 0; $k -lt $files.Length; $k++) {
    $rel = $files[$k]
    $srcPath = Join-Path $root ($rel -replace '/', '\')
    if (-not (Test-Path $srcPath)) { throw "missing source file: $srcPath" }
    $b64f = [Convert]::ToBase64String([IO.File]::ReadAllBytes($srcPath))
    $parent = $rel.Substring(0, $rel.LastIndexOf('/'))
    $tag = "B64FILE$k"
    $inject += 'mkdir -p "$S/' + $parent + '"' + "`n"
    $inject += 'base64 -d > "$S/' + $rel + '" <<' + "'$tag'" + "`n" + $b64f + "`n" + $tag + "`n"
}

# ---- static bash: head (md5 before, conftest, scratch + copy real box config) ----
$bashHead = @'
set +e
S="$HOME/pm_p1_scratch"
OUT=/tmp/pm_bc_out.txt
LEGACY=/home/azureuser/trading_corp/data/trading_corp.db
{
echo "===== LEGACY DB MD5 + STAT (BEFORE) ====="
md5sum "$LEGACY"; stat -c "%n size=%s mtime=%y" "$LEGACY"
echo "===== repo-root conftest.py? ====="
if [ -f /home/azureuser/trading_corp/conftest.py ]; then echo "ROOT_CONFTEST_EXISTS:"; cat /home/azureuser/trading_corp/conftest.py; else echo "(no repo-root conftest.py)"; fi
echo "===== tests/conftest.py (REAL, from box) ====="
cat /home/azureuser/trading_corp/tests/conftest.py
echo "===== SCRATCH SETUP ($S) ====="
rm -rf "$S"
mkdir -p "$S/trading_corp/prediction_markets" "$S/tests/prediction_markets"
cp /home/azureuser/trading_corp/pyproject.toml "$S/pyproject.toml"
cp /home/azureuser/trading_corp/tests/conftest.py "$S/tests/conftest.py"
cp /home/azureuser/trading_corp/trading_corp/__init__.py "$S/trading_corp/__init__.py"
'@

# ---- static bash: mid (find, pytest, gamma probe, md5 after, cleanup) ----
$bashMid = @'
echo "===== SCRATCH FILES ====="
find "$S" -type f | sort
echo "===== PYTEST tests/prediction_markets/ (PM_DB_PATH=tmp, box venv) ====="
cd "$S" && PM_DB_PATH="/tmp/pm_test_$$.db" PYTHONPATH=. /home/azureuser/trading_corp/venv/bin/python -m pytest tests/prediction_markets/ -q -p no:pytest_ethereum -p no:cacheprovider
echo "pytest_exit=$?"
rm -f /tmp/pm_test_*.db
echo "===== GAMMA TAGS PROBE (read-only) ====="
/home/azureuser/trading_corp/venv/bin/python3 - <<'PYGAMMA'
import json, urllib.request
DATA="https://data-api.polymarket.com"; GAMMA="https://gamma-api.polymarket.com"
def http(u):
    r=urllib.request.Request(u, headers={"User-Agent":"pm-gamma-probe/1.0"})
    with urllib.request.urlopen(r, timeout=30) as x: return json.loads(x.read().decode())
PREF=["fed-decision","fed-interest-rates","fed-rate","fed","mlb","nba","nfl","nhl","ufc","cs2","atp","wta","cbb","fifwc","epl","ucl","wnba","nascar"]
def catof(es):
    s=(es or "").lower()
    for p in sorted(PREF,key=len,reverse=True):
        if s==p or s.startswith(p+"-"): return "fed" if p.startswith("fed") else p
    return "unknown"
WALLETS=[("evanng","0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618"),("pako","0x71edffd0d70a1da823ff07a3c6fc81457294d338")]
want={"mlb":None,"ufc":None,"nba":None,"fed":None}; unknowns=[]
for nm,w in WALLETS:
    for off in range(0,600,50):
        try: rows=http("%s/closed-positions?user=%s&limit=50&offset=%d"%(DATA,w,off))
        except Exception as e: print("pull err",str(e)[:60]); break
        if not rows: break
        for r in rows:
            es=r.get("eventSlug") or r.get("slug") or ""; cid=r.get("conditionId")
            if not cid: continue
            c=catof(es)
            if c in want and want[c] is None: want[c]=(cid,es)
            elif c=="unknown" and len(unknowns)<2 and es and cid not in [u[0] for u in unknowns]: unknowns.append((cid,es))
        if all(want.values()) and len(unknowns)>=2: break
        if len(rows)<50: break
    if all(want.values()) and len(unknowns)>=2: break
samples=[(c,v[0],v[1]) for c,v in want.items() if v]+[("unknown",cid,es) for cid,es in unknowns]
print("SAMPLE cids (from real /closed-positions):")
for c,cid,es in samples: print("  [%s] %s eventSlug=%s"%(c,cid,es))
print("")
for c,cid,es in samples:
    try: ms=http("%s/markets?condition_ids=%s&closed=true"%(GAMMA,cid))
    except Exception as e: print("[%s] %s GAMMA ERR %s"%(c,cid[:14],str(e)[:60])); continue
    if not ms:
        try: ms=http("%s/markets?condition_ids=%s"%(GAMMA,cid))
        except Exception: ms=[]
    m=ms[0] if ms else {}
    print("--- [%s] cid=%s ---"%(c,cid[:24]))
    print("  market keys:", sorted(m.keys()))
    print("  tags:", json.dumps(m.get("tags"), default=str)[:700])
    for k in ("category","categories","slug","seriesSlug","groupItemTitle"):
        if k in m: print("  %s=%s"%(k, json.dumps(m.get(k),default=str)[:150]))
print("")
print("FIRST market FULL (all fields, schema reference):")
if samples:
    c,cid,es=samples[0]
    try:
        ms=http("%s/markets?condition_ids=%s&closed=true"%(GAMMA,cid)) or http("%s/markets?condition_ids=%s"%(GAMMA,cid))
        print(json.dumps((ms or [{}])[0], default=str, indent=1)[:2000])
    except Exception as e: print("err",str(e)[:60])
PYGAMMA
echo "===== LEGACY DB MD5 + STAT (AFTER) ====="
md5sum "$LEGACY"; stat -c "%n size=%s mtime=%y" "$LEGACY"
echo "===== SCRATCH CLEANUP ====="
rm -rf "$S"
if [ -d "$S" ]; then echo "SCRATCH_STILL_PRESENT_FAIL"; else echo "SCRATCH_REMOVED_OK"; fi
} > "$OUT" 2>&1
echo "RUN_DONE lines=$(wc -l < "$OUT") bytes=$(wc -c < "$OUT")"
'@

# ---- assemble, ship (base64 chunks), run to a file, chunk-retrieve, clean up ----
$box = $bashHead + $inject + $bashMid
$box = $box -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($box))
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_bc_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pm_bc.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pm_bc.b64 > /tmp/pm_bc.sh && bash /tmp/pm_bc.sh`n", $enc)
Write-Host "== PM P1 BOX TEST + GAMMA PROBE (READ-ONLY, isolated scratch) =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
$lines = 600
$runStr = ($runMsg | Out-String)
if ($runStr -match 'lines=(\d+)') { $lines = [int]$Matches[1] }
$per = 30
$nchunks = [math]::Ceiling(($lines + 2) / $per)
for ($n = 0; $n -lt $nchunks; $n++) {
    $a = $n * $per + 1
    $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_bc_out.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
[IO.File]::WriteAllText($tf, "rm -f /tmp/pm_bc.b64 /tmp/pm_bc.sh /tmp/pm_bc_out.txt`n", $enc)
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
Remove-Item $tf -ErrorAction SilentlyContinue
