D=/tmp/pmc_bandC
PY=/home/azureuser/trading_corp/venv/bin/python
echo "########## BAND C BOX-SCRATCH TEST (no live tree touched) ##########"
echo "df / before:"; df -h / | tail -1
rm -rf "$D"; mkdir -p "$D"
tar -xzf /tmp/pmc_bandC.tar.gz -C "$D" && echo "extracted to $D"
cd "$D" || { echo "FATAL cd"; rm -f /tmp/pmc_bandC.tar.gz; exit 2; }
echo "=== py_compile 3 changed files ==="
"$PY" -m py_compile trading_corp/prediction_markets/web/marks.py trading_corp/prediction_markets/web/poller.py trading_corp/prediction_markets/web/app.py && echo "py_compile: OK" || echo "py_compile: FAIL"
echo "=== import gate (marks + poller from scratch tree) ==="
PYTHONPATH="$D" "$PY" -c "from trading_corp.prediction_markets.web import marks,poller; print('import OK; has fetch_marks_by_ticker=%s fetch_ticker_mark=%s' % (hasattr(marks,'fetch_marks_by_ticker'), hasattr(marks,'fetch_ticker_mark')))"
echo "=== pytest: new by-ticker test + poller(milestones) test ==="
PYTHONPATH="$D" "$PY" -m pytest -p no:pytest_ethereum -q tests/prediction_markets/test_marks_by_ticker_2026_09_27.py tests/prediction_markets/test_milestones.py 2>&1 | tail -25
echo "=== cleanup ==="
cd /; rm -rf "$D" /tmp/pmc_bandC.tar.gz
echo "cleaned. df / after:"; df -h / | tail -1
echo "########## BAND C TEST COMPLETE ##########"
