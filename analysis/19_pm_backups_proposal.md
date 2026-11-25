# pm_* backups -- deletion PROPOSAL (Jack rules; NOTHING deleted this run) -- 2026-11-24

Full list + sizes/dates: `14_recon.txt` lines 149-450. Total **4.3G** across ~130 `pm_*backup*` items in /home/azureuser.
Most are tiny 4K marker dirs or small .json (negligible). The space is in ~25 `.db` / large deploy-dir backups.
Rule applied: KEEP the newest-per-family + anything that is (or could be) a rollback point; propose deleting
old, superseded snapshots. Cross-referenced names with vault/MEMORY where possible.

## KEEP (recent / rollback points)
| item | size | date | why keep |
|---|---|---|---|
| pm_recovery_backup_20261007T032558Z.db (+shm/wal) | 640M | 10-07 | "recovery" DB snapshot; most recent large; likely a rollback point |
| pm_legaudit_clear_backup_20261008T000933Z.db | 640M | 10-08 | recent leg-audit-clear DB (MEMORY references leg-audit); keep as rollback |
| pm_rollupfix_backup_20261011T002054Z.tar | 296K | 10-11 | newest; tiny; MEMORY-referenced (pm-rollup-fix deploy) |
| pm_wlrefresh / pm_settclose / all <1M .json + 4K marker dirs | ~tens of MB | Aug-Oct | negligible; not worth the risk; keep |

## TIER A -- RECOMMEND DELETE (Aug, earliest stage/caps/migration snapshots, fully superseded) ~0.78G
| item(s) | size | date |
|---|---|---|
| pm_stage0_rung3 / pm_stage1_rung1 / pm_stage1_rung3_adj / pm_stage1_rung3_poll / pm_t1_rung2_poll / pm_rollup2 _dbbackup*.db (+shm/wal) | 24M x6 = ~144M | 2026-08-27 |
| pm_caps_set / pm_caps_write / pm_mig014 / pm_reattach / pm_rd_deploy / pm_r5_caps _backup*.db | 102M x6 = ~612M | 2026-08-30/31 |

## TIER B -- PROPOSE DELETE (Sept; superseded; Jack confirm none is a named rollback point) ~2.3G
| item | size | date | note |
|---|---|---|---|
| pm_analyze_backup_20260913 | 376M | 09-13 | Sept analyze snapshot |
| pm_deploy11_backup_20260912 (dir) | 375M | 09-12 | superseded by deploy12-16 |
| pm_mig021_backup_20260910.db | 320M | 09-10 | migration-021 backup; DB now schema 25 |
| pm_enable_nfljack_backup_20260910.db | 319M | 09-10 | NFL-enable backup |
| pm_enable_nflkaren_backup_20260910.db | 319M | 09-10 | NFL-enable backup |
| pm_bundle_backup x3 (195811Z/200119Z/201551Z) | 103M x3 | 09-03 | Sept bundle deploy backups |
| pm_r4_karencaps / pm_lossomit_deploy / pm_liveness_stage1b _backup | 103M x3 | 09-02/06 | Sept deploy backups |

## Not proposed (keep for now)
All other `pm_deployN_backup` 4K marker dirs, graft/enable .json, and <300K dirs -- negligible footprint; deleting
them is churn with little reclaim. Revisit only if disk pressure returns.

## Reclaim if approved
- TIER A only: ~0.78G (84%->~79%).
- TIER A + B: ~3.1G (84%->~75%).
Disk is currently 80% after the Batch-5 journald trim, so this is optional headroom, not urgent.

## PROPOSED delete command (NOT run; for Jack to approve as a Batch-5b script)
A future `secrev_pm_prune.sh` would `rm -rf` ONLY the explicitly-listed Tier-A (and, if Jack approves, Tier-B)
paths, after re-confirming each is not newer than the kept rollback points. No wildcards. Nothing executed now.
