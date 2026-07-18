# RH-auth batch ROLLBACK (staged BEFORE apply, per operator). Operator paste (ONE line):
#   powershell -ep bypass -f .\deploy_rh_auth\rollback_rh_auth_batch.ps1
# Restores every *.bak_rhauth the patcher wrote, removes the new template, py_compiles the
# restored files. Then RESTART the engine to activate the rollback (restart is separate).
$ErrorActionPreference = 'Stop'
$h = 'azureuser@trading.jacksumner.com'
$cmd = 'T=/home/azureuser/trading_corp/trading_corp; for f in utils/secrets.py brokers/robinhood.py agents/data_exec.py web/routes.py main.py; do if [ -f "$T/$f.bak_rhauth" ]; then mv "$T/$f.bak_rhauth" "$T/$f" && echo "restored $f"; else echo "no .bak for $f (skip)"; fi; done; rm -f "$T/web/templates/rh_session_panel.html" && echo "removed rh_session_panel.html"; cd /home/azureuser/trading_corp; for f in utils/secrets.py brokers/robinhood.py agents/data_exec.py web/routes.py main.py; do PYTHONPATH=/home/azureuser/trading_corp venv/bin/python -m py_compile "trading_corp/$f" && echo "compiled $f" || echo "COMPILE FAIL $f"; done; echo "ROLLBACK DONE - restart the engine to activate"'
$cmd | ssh $h "tr -d '\r'|bash"
