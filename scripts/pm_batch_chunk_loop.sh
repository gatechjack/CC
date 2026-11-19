#!/bin/bash
# ONE-TIME chunk relauncher for scripts/pm_batch_reanalyze.py (Board-authorized 2026-11-19; NOT a schedule --
# no cron entry may point here). Deployed to /home/azureuser/pmbatch_chunk_loop.sh on tc-prod-vm and run once
# under setsid+nohup with KEY_VAULT_URI in the environment (the non-secret vault URI from the pm_web unit).
#
# Jack addendum 2026-11-19 ~22:10Z -- protect the LIVE engine from the batch's memory:
#   1. every chunk raises its own oom_score_adj to 1000 BEFORE exec: the kernel's first OOM victim is
#      always the batch, never the engine (the 21:31Z killer chose the batch only by size).
#   2. 2GB address-space cap per chunk (ulimit -v) + 3-wallet chunks. A wallet that cannot fit retries
#      once as a fresh 1-wallet process (runner rc=3 on MemoryError), then fails closed. Cap never raised.
# rc contract with the runner: 3 = chunk done or fresh-process retry requested (relaunch);
# 0 = batch complete; anything else = guardrail stop or error (do NOT relaunch -- report).
cd /home/azureuser/trading_corp || exit 9
CL=/home/azureuser/pmbatch_chunks.log   # deliberately NOT pm_*.log (the runner greps that glob for SQLITE_BUSY)
for i in $(seq 1 160); do
  bash -c 'echo 1000 > /proc/self/oom_score_adj; ulimit -v 2000000; export PYTHONPATH=.;
           exec venv/bin/python scripts/pm_batch_reanalyze.py --max-wallets 3' \
    >> /home/azureuser/pm_batch_reanalyze.log 2>&1
  rc=$?
  echo "$(date -u +%FT%TZ) chunk $i rc=$rc" >> "$CL"
  if [ $rc -eq 3 ]; then continue; fi
  if [ $rc -eq 0 ]; then echo "$(date -u +%FT%TZ) BATCH COMPLETE" >> "$CL"; exit 0; fi
  echo "$(date -u +%FT%TZ) STOP rc=$rc (guardrail stop or error -- not relaunching)" >> "$CL"; exit $rc
done
echo "$(date -u +%FT%TZ) chunk budget exhausted (160)" >> "$CL"; exit 4
