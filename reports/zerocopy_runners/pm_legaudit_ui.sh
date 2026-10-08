ROOT=/home/azureuser/trading_corp
echo "=== A: pm_web 'leg audits to review' / code_review surface (box code) ==="
grep -rnE "to review|to_review|code_review|leg_audit|LEG AUDIT|legaudit|needs_review|REVIEW" "$ROOT/trading_corp/prediction_markets/web" --include=*.py --include=*.html 2>/dev/null | grep -viE '/__pycache__/' | head -60
echo "=== B: leg_audit module (classify + CLEAN/REVIEW sets) ==="
ls -1 "$ROOT/trading_corp/prediction_markets" | grep -iE 'leg_audit|legaudit' || echo "(no leg_audit*.py in prediction_markets)"
for f in "$ROOT/trading_corp/prediction_markets/leg_audit.py"; do
  if [ -f "$f" ]; then echo "--- $f (classify / CLEAN / review) ---"; grep -nE "def classify|CLEAN|code_review|ok:|code_pad|REVIEW|def .*review|subsequence|in_outcome" "$f" | head -50; fi
done
echo "=== C: any acknowledge/dismiss/clear path (pm_cli / app route / column) ==="
grep -rnE "acknowledg|dismiss|clear.*audit|audit.*clear|review.*ack|mark.*review|reviewed" "$ROOT/trading_corp/prediction_markets" "$ROOT/trading_corp/scripts/pm_cli.py" --include=*.py 2>/dev/null | grep -viE '/__pycache__/' | head -30
echo "=== D: how the COUNT is computed (grep the count/badge) ==="
grep -rnE "code_review|to review|audits? to|n_review|review_count|REVIEW" "$ROOT/trading_corp/prediction_markets/web" --include=*.py 2>/dev/null | grep -iE "count|sum|len|n_|badge|where|filter" | head -30
echo "=== DONE legaudit_ui ==="
