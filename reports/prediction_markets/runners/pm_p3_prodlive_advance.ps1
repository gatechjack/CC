$OutputEncoding = New-Object System.Text.UTF8Encoding $false
# PM P3 prod-live advance -- a SEPARATE, deliberate LEDGER step (decoupled from the deploy).
# Run this ONLY after you have READ the pm_p3_deploy.ps1 output and agree the box deploy is right
# (DEPLOY_VERDICT=OK + GATE_PASS + HEALTHZ_OK + ENGINE_PID_UNCHANGED=GOOD). Records the 11 deployed PM
# artifacts on the prod-live ledger (byte-identical to what was deployed, == box). Fail-safe: STOPS if the
# prod-live worktree is not clean and exactly on origin/prod-live -- it never force-touches the ledger.
# Operator pastes ONE line:  powershell -ep bypass -f .\pm_p3_prodlive_advance.ps1
$branch = "prediction-markets-p3-2026-08-24"
$plwt = "C:\Users\AA Incorporado\cc-prodlive-cp7-wt"
$files = @(
  "trading_corp/prediction_markets/positions.py",
  "trading_corp/prediction_markets/names.py",
  "trading_corp/prediction_markets/stats.py",
  "trading_corp/prediction_markets/web/app.py",
  "trading_corp/prediction_markets/web/static/pm.css",
  "trading_corp/prediction_markets/web/templates/pm_macros.html",
  "trading_corp/prediction_markets/web/templates/pm_whale.html",
  "trading_corp/prediction_markets/web/templates/pm_whale_overview.html",
  "trading_corp/prediction_markets/web/templates/partials/pm_position_rows.html",
  "trading_corp/prediction_markets/web/templates/partials/pm_scoreboard_table.html",
  "trading_corp/scripts/pm_cli.py"
)
if (-not (Test-Path $plwt)) { Write-Host "prod-live worktree missing at $plwt - STOP (create it: git worktree add $plwt prod-live)"; exit 1 }
git -C "$plwt" fetch origin 2>&1 | Out-Null
$cur = (git -C "$plwt" rev-parse --abbrev-ref HEAD)
$dirty = (git -C "$plwt" status --porcelain)
$tip = (git -C "$plwt" rev-parse HEAD)
$otip = (git -C "$plwt" rev-parse origin/prod-live)
if ($cur -ne 'prod-live') { Write-Host ("worktree is on '" + $cur + "', not prod-live - STOP (git -C '" + $plwt + "' checkout prod-live)"); exit 1 }
if ($dirty) { Write-Host "prod-live worktree is DIRTY - STOP; resolve before advancing:"; Write-Host $dirty; exit 1 }
if ($tip -ne $otip) { Write-Host ("prod-live tip " + $tip.Substring(0,7) + " != origin/prod-live " + $otip.Substring(0,7) + " - STOP; reconcile first"); exit 1 }
Write-Host ("prod-live clean + on anchor " + $otip.Substring(0,7) + "; recording the 11 deployed PM artifacts from " + $branch + "...")
git -C "$plwt" checkout $branch -- @files
if ($LASTEXITCODE -ne 0) { Write-Host "checkout of artifacts FAILED - STOP"; exit 1 }
git -C "$plwt" add @files
git -C "$plwt" diff --cached --quiet
if ($LASTEXITCODE -eq 0) { Write-Host "prod-live already carries these exact bytes - nothing to advance."; exit 0 }
Write-Host "staged artifacts (prod-live will record):"
git -C "$plwt" diff --cached --stat
git -C "$plwt" commit -m "deploy(pm-p3): record CP2 Phase-3 artifacts on prod-live (== box)"
if ($LASTEXITCODE -ne 0) { Write-Host "COMMIT FAILED - STOP"; exit 1 }
git -C "$plwt" push origin prod-live 2>&1 | Select-Object -Last 2 | Write-Host
Write-Host ("prod-live advanced -> " + (git -C "$plwt" rev-parse --short HEAD) + " (main + the deploy branch untouched)")
