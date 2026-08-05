# Worktree prune — executed 2026-08-05 (ops session 3, mechanic ii)

Operator-authorized (NOT-LIVE confirmed for cc-2026-08-04b + mechanic ii). Method: delete the
audited non-ignored untracked scratch, then plain `git worktree remove` (NO --force), KEEP all
branches (no -D). Empirically verified on cc-htf-sweep-wt first. **0 tracked modifications in any**
of the 15 (nothing committed-but-unsaved was lost). **Worktree count 86 -> 71 (15 removed).**
All 15 branches KEPT. ⚠ cc-2026-08-04b-wt + branch were removed EXTERNALLY (not by this session —
the 15 removes were exactly the list below; branch-deletion needs an explicit -D never run here).

Count correction: the audit TABLE marks **15** DISCARD (session-2 prose miscounted "14";
6 COMMIT + 15 DISCARD + 1 KEEP = 22). All 15 pruned below.

## Itemized scratch deleted (188 files across 15 worktrees)

```
### bitunix-scalp-tp-recalibration-2026-06-10 : 1 audited-scratch files
    tpdata.out
### bitunix-sfp-2026-06-25 : 48 audited-scratch files
    _prodsnap/branch_analysis.tsv
    _prodsnap/cockpit_postdeploy.md5
    _prodsnap/cockpit_postdeploy2.md5
    _prodsnap/cockpit_prod.md5
    _prodsnap/collect.txt
    _prodsnap/futobs.py.prod
    _prodsnap/futobs.py.staged
    _prodsnap/gen_fetch.py
    _prodsnap/groupA.txt
    _prodsnap/home.html.prod
    _prodsnap/home.html.staged
    _prodsnap/home_verify.py
    _prodsnap/live_bar_cache.py.prod
    _prodsnap/main.py.p3staged
    _prodsnap/main.py.prod
    _prodsnap/main.py.staged
    _prodsnap/p1_futobs.patch
    _prodsnap/p1_main.patch
    _prodsnap/p3_main.patch
    _prodsnap/prod_inventory.md5
    _prodsnap/recon_collect.txt
    _prodsnap/recon_compare.py
    _prodsnap/recon_fetch.txt
    _prodsnap/recon_full.txt
    _prodsnap/recon_reconcile.tgz
    _prodsnap/recon_suite.txt
    _prodsnap/recon_suite2.txt
    _prodsnap/render_test.py
    _prodsnap/restart_guarded.sh
    _prodsnap/routes.py.prod
    _prodsnap/sfp_observer.py.prod
    _prodsnap/sfp_observer.tpfix-staged
    _prodsnap/strategies.yaml.disarm
    _prodsnap/strategies.yaml.prod
    _prodsnap/strategies.yaml.staged
    _prodsnap/suite_A_full.txt
    _prodsnap/suite_A_reconciled.txt
    _prodsnap/tpfix_observer.patch
    _prodsnap/undeployed_inventory.py
    _prodsnap/verify2.sh
    _prodsnap/verify3.sh
    _prodsnap/verify4.sh
    _prodsnap/verify5.sh
    _prodsnap/ws_probe.py
    deploy/2026-06-27_sfp_cockpit/ethfix.sh
    deploy/2026-06-27_sfp_tpfix/staged/bitunix_sfp_observer.py
    scripts/_expA2_run.log
    scripts/_expB2_run.log
### bitunix-untaken-trades-deep-dive-2026-06-10 : 1 audited-scratch files
    qdata.out
### htf-proximity-audit-2026-06-01 : 4 audited-scratch files
    .scratch/htf_proximity_audit/bars_3m.tsv
    .scratch/htf_proximity_audit/rejections.tsv
    .scratch/htf_proximity_audit/rejections_enriched.tsv
    .scratch/htf_proximity_audit/scope_out.tsv
### stage1-redeploy-2026-05-30 : 31 audited-scratch files
    .scratch/az_chunked_upload.py
    .scratch/build_redeploy_payload.py
    .scratch/gate_c_md5diff.log
    .scratch/postrollback.txt
    .scratch/probe1.txt
    .scratch/probe2.txt
    .scratch/probe3.txt
    .scratch/probe4.txt
    .scratch/pytest_phase1_full.log
    .scratch/redeploy/chunks/000.b64
    .scratch/redeploy/chunks/001.b64
    .scratch/redeploy/chunks/002.b64
    .scratch/redeploy/chunks/003.b64
    .scratch/redeploy/chunks/004.b64
    .scratch/redeploy/chunks/005.b64
    .scratch/redeploy/chunks/006.b64
    .scratch/redeploy/chunks/007.b64
    .scratch/redeploy/chunks/008.b64
    .scratch/redeploy/chunks/009.b64
    .scratch/redeploy/chunks/010.b64
    .scratch/redeploy/chunks/011.b64
    .scratch/redeploy/chunks/012.b64
    .scratch/redeploy/chunks/013.b64
    .scratch/redeploy/manifest.txt
    .scratch/redeploy/phase_c_extract_deploy.sh
    .scratch/redeploy/plan.json
    .scratch/redeploy/rollback.sh
    .scratch/redeploy/stage1.tgz
    .scratch/redeploy/stage1.tgz.b64
    .scratch/rollback.txt
    .scratch/upload.log
### cc-2026-07-29-wt : 64 audited-scratch files
    auth1.sh
    auth1_out.txt
    auth1_run.ps1
    auth2.sh
    auth2_out.txt
    auth2_run.ps1
    auth_diag_payload.sh
    auth_diag_payload_lf.sh
    auth_fsdiag_payload.sh
    auth_fsdiag_payload_lf.sh
    auth_geturl_payload.sh
    auth_geturl_payload_lf.sh
    auth_life2_payload.sh
    auth_life2_payload_lf.sh
    auth_lifespan_payload.sh
    auth_lifespan_payload_lf.sh
    auth_raw_payload.sh
    auth_raw_payload_lf.sh
    dep_apply.sh
    dep_apply_out.txt
    dep_apply_run.ps1
    dep_bt.b64
    dep_press.b64
    dep_restart.sh
    dep_restart_out.txt
    dep_restart_run.ps1
    dep_strat.b64
    dep_tbt.b64
    dep_tpr.b64
    dep_transfer.ps1
    dep_tsd.b64
    dep_verifyA.sh
    dep_verifyA2.sh
    dep_verifyA2_out.txt
    dep_verifyA2_run.ps1
    dep_verifyA_out.txt
    dep_verifyA_run.ps1
    dep_verifyB.sh
    dep_verifyB_out.txt
    dep_verifyB_run.ps1
    deploy_kct.b64
    deploy_main.b64
    deploy_yaml.b64
    pead_ctx.sh
    pead_ctx_out.txt
    pead_ctx_run.ps1
    pead_disc.sh
    pead_disc_out.txt
    pead_disc_run.ps1
    pead_gatea.sh
    pead_gatea_out.txt
    pead_gatea_run.ps1
    pead_gates.sh
    pead_gates_out.txt
    pead_gates_run.ps1
    pead_health.sh
    pead_health_out.txt
    pead_health_run.ps1
    pead_p2a.sh
    pead_p2a_out.txt
    pead_p2a_run.ps1
    pead_stx.sh
    pead_stx_out.txt
    pead_stx_run.ps1
### cc-2026-07-31-wt : 6 audited-scratch files
    _sfp_fix/board_after.html
    _sfp_fix/prod/sfp_cockpit_view.py
    _sfp_fix/prod/sfp_cockpit_view.py.fresh
    _sfp_fix/prod/sfp_construct_cockpit_view.py
    _sfp_fix/prod/sfp_llm_analysis_view.py
    _sfp_fix/sfp_cockpit_view.py.new
### cc-2026-07-31d-wt : 2 audited-scratch files
    _p2_deploy_root.sh
    _p2_rollback.sh
### cc-2026-08-01b-wt : 2 audited-scratch files
    kc2_pull.ps1
    kcv2_s0_check.ps1
### cc-2026-08-02b-wt : 16 audited-scratch files
    _deploy_bundle/apply.sh
    _deploy_bundle/apply_az.sh
    _deploy_bundle/files/config/strategies.yaml
    _deploy_bundle/files/trading_corp/agents/strategies/pead_sizing.py
    _deploy_bundle/files/trading_corp/agents/strategies/pead_strategy.py
    _deploy_bundle/files/trading_corp/web/pead_view.py
    _deploy_bundle/files/trading_corp/web/templates/partials/pead_dial.html
    _deploy_bundle/files/trading_corp/web/templates/partials/pead_live_sections.html
    _deploy_bundle/files/trading_corp/web/templates/pead_live.html
    _deploy_bundle/patches/base.patch
    _deploy_bundle/patches/earnings_provider.patch
    _deploy_bundle/patches/robinhood.patch
    _deploy_bundle/patches/routes.patch
    _deploy_bundle/verify_full.sh
    _deploy_bundle/verify_md5.py
    pead_deploy.tar.gz
### cc-2026-08-02c-wt : 5 audited-scratch files
    _cd/_pead_card.html
    _cd/pead_live.html
    _cd/pead_live_sections.html
    _cd/pead_view.py
    _cd/test_pead_card.py
### cc-bull-bottleneck-wt : 1 audited-scratch files
    data/bull_bottleneck/cleared_buy.csv
### cc-claude-2026-07-26-wt : 3 audited-scratch files
    deploy_tmp/data.py.lf
    deploy_tmp/kalshi_resolver.py.lf
    deploy_tmp/pmbody.html.lf
### cc-htf-sweep-wt : 2 audited-scratch files
    data/htf_sweep/htf_gate_decision.csv
    data/htf_sweep/score_decided.csv
### cc-sfp-deploy-wt : 2 audited-scratch files
    deploy_bidirectional_sfp/_fullsuite_after_reconcile.txt
    deploy_bidirectional_sfp/preflight_prod_snapshot.txt
```
