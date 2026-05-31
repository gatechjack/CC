# Prod vs origin/main file-level md5 sweep

Surface: trading_corp/ + config/  (251 expected files)

## Summary

- MATCH:                          185
- DIFFER-EXPECTED-PER-DEPLOY-LOG: 1
- DIFFER-STALE-ON-PROD:           51
- MISSING_ON_PROD:                14
- PROD_ONLY_NOT_ON_MAIN:          18

## DIFFER-EXPECTED-PER-DEPLOY-LOG (known sed-overlays)

### `config/strategies.yaml`

- expected (origin/main LF): `1fda7f608c1e74900b55eb77f0bb344f`
- actual (prod):             `61dd355082f936016810337058d30cd0`
- ref:                       deploy_log.md 2026-05-30 03:57 UTC (branch bitunix-risk-tier-pre-live, NOT merged)
- reason:                    BitUnix paper-mode tier sizing aligned with intended live values (PREMIUM 0.04/8x → 0.015/25x, STANDARD 0.02/5x → 0.0075/25x). Sed-overlay on prod; main carries the old values. Future deploy of main without re-applying the sed would silently revert sizing.

## DIFFER-STALE-ON-PROD (must be in next deploy's transfer set)

- `config/divisions.yaml`
    expected (origin/main LF): `c0b3caa54327c2709e69a4129790e51b`
    actual (prod):             `3e14dc5dccacac9e3fa3b45c4a04b165`
- `config/risk.yaml`
    expected (origin/main LF): `8296e915514fb58b5b1b97b650b05ebb`
    actual (prod):             `1d053d426dd31add7153884b4339e7e9`
- `config/weather_stations.yaml`
    expected (origin/main LF): `00a31185cdff99c33c5743f4c153df56`
    actual (prod):             `aba9856e13a197927fe73913db2d9232`
- `trading_corp/agents/backtester.py`
    expected (origin/main LF): `8492b9dd116f40f112d29f233fedac6a`
    actual (prod):             `4d1a74b17b390c9bf2af383cc1ba7d9c`
- `trading_corp/agents/data_exec.py`
    expected (origin/main LF): `e3e4cca7a701a6add22ab43514906c6f`
    actual (prod):             `a67b89c3508af462671836b04682757a`
- `trading_corp/agents/divisions/bitunix_futures_observer.py`
    expected (origin/main LF): `36863e71624fd5126a9c6e433b68e81b`
    actual (prod):             `ec2a0f74fb51001d9e58f7616a25f9de`
- `trading_corp/agents/divisions/fidelity_options.py`
    expected (origin/main LF): `da9db30e1f490eb31f3535dc6907b7b0`
    actual (prod):             `821f983b0adae61da31a98c451b0e4be`
- `trading_corp/agents/logger.py`
    expected (origin/main LF): `2938e089da5199b133854444893bdd02`
    actual (prod):             `0b409660655855a0a373f16a0fa8fd00`
- `trading_corp/agents/risk.py`
    expected (origin/main LF): `4b87e1497da62051f109a8dcd28558f3`
    actual (prod):             `dfe9f54c71da183e2ad2f5909323f012`
- `trading_corp/agents/strategies/_weather_math.py`
    expected (origin/main LF): `9b83481f497b5235180e7c2725384615`
    actual (prod):             `7a025622345e25c73b9f0ce23d7e0968`
- `trading_corp/agents/strategies/bitunix_confluence.py`
    expected (origin/main LF): `3e8430f0b9734eab8a7c1cb1b7b4eb50`
    actual (prod):             `9fb825d4a030b622fd283b779986cea7`
- `trading_corp/agents/strategies/bitunix_pa_validation.py`
    expected (origin/main LF): `3206ab800191a9d5279da1580bc07590`
    actual (prod):             `49b965fabe110494e87db5257740547c`
- `trading_corp/agents/strategies/btc_accumulator.py`
    expected (origin/main LF): `b4f2a0c599c2a086488c3758148321c2`
    actual (prod):             `f158be7e8dc242b21b6fee1b3ed74736`
- `trading_corp/agents/strategies/ic_candidate_grader.py`
    expected (origin/main LF): `bd139fc418012c0173856b825dccfcce`
    actual (prod):             `df7a23783bae06bcd92bf6609b18b3ae`
- `trading_corp/agents/strategies/kalshi_llm_arbitrage.py`
    expected (origin/main LF): `6ef08f28060d2cbb64b83e45a4d3b5b4`
    actual (prod):             `6b51f64837fa7a39a24e2aefd83319d0`
- `trading_corp/agents/strategies/kalshi_sports_scout.py`
    expected (origin/main LF): `d2e859801f3de67e2f98dbf6a69efb39`
    actual (prod):             `f9baeb51adda3419ec444cb79b6422a6`
- `trading_corp/agents/strategies/polymarket_arbitrage.py`
    expected (origin/main LF): `8b2bb008bd3dbdce90fc980806860059`
    actual (prod):             `7965e45122d05033c366246d2d0a4620`
- `trading_corp/brokers/bitunix.py`
    expected (origin/main LF): `4c1dd0587f62957c037bff65fe2c09c6`
    actual (prod):             `61b406fa218900b15e5f2d2366cc7579`
- `trading_corp/brokers/kalshi.py`
    expected (origin/main LF): `d65f59034a6a363784818c425252f4b5`
    actual (prod):             `9372693bde03498adda9b3f79d461edf`
- `trading_corp/comms/bitunix_lifecycle_notifier.py`
    expected (origin/main LF): `2e89db0450e4df7006cfa2c44e7409e4`
    actual (prod):             `065534d22d90692f1a88f03b70aad785`
- `trading_corp/comms/telegram_commands.py`
    expected (origin/main LF): `3b31baba1da0d53b264304b46db223a8`
    actual (prod):             `6862042590d58f81fe786a0e362529e0`
- `trading_corp/data/bitunix_bar_archiver.py`
    expected (origin/main LF): `f83a305f0d096503ea308cacbaa08ef0`
    actual (prod):             `4621246eb64e543013622aa04688a03b`
- `trading_corp/data/bitunix_htf_context.py`
    expected (origin/main LF): `9de5da1b2c4fbd861244dcfa366c3ac6`
    actual (prod):             `80ae3cfbeb42c680a2bef9e32c181a8b`
- `trading_corp/data/kalshi_market_map.py`
    expected (origin/main LF): `34c57e12f342c6e82d7f5bfd94749757`
    actual (prod):             `bd1fc0cdf9649c8579c39de1f7b0cfab`
- `trading_corp/data/kalshi_whale_stats.py`
    expected (origin/main LF): `5a7aae094242bca1af261b607b01d008`
    actual (prod):             `b2abe6a90fd0e0c9062404b4ed8787fc`
- `trading_corp/data/weather_stations.py`
    expected (origin/main LF): `8656ff20f01abe827955f535f0eab2b5`
    actual (prod):             `2c485ab60e3b5c27ce25ed47a2c962bd`
- `trading_corp/graph/ceo_graph.py`
    expected (origin/main LF): `f392827837d44c8e482d6dbf9821f761`
    actual (prod):             `9038855877008dce274f534efcfc6eb9`
- `trading_corp/main.py`
    expected (origin/main LF): `b7c0804566fbe22559cf8b9e3c864d53`
    actual (prod):             `e9b6da138c915d25dbecb857537e51cb`
- `trading_corp/persistence/db.py`
    expected (origin/main LF): `1820da79b296c5f22b235d5b33be832f`
    actual (prod):             `1782cc9cf89c46e9d19848263b8b1b96`
- `trading_corp/persistence/models.py`
    expected (origin/main LF): `516108fc2e65ef011b9963852cc07b2d`
    actual (prod):             `71108b3342ca0b3d4912fec2055f4356`
- `trading_corp/utils/divisions.py`
    expected (origin/main LF): `c38b02fccd0c56394e9e484d3c0e1d3e`
    actual (prod):             `f301099abe4db3251269a3986280e3cd`
- `trading_corp/utils/secrets.py`
    expected (origin/main LF): `983146eefe5b138769653b994750e801`
    actual (prod):             `ad434ab24e259524f8cdc026869063e2`
- `trading_corp/web/app.py`
    expected (origin/main LF): `824195a602c636065426f14444067f7a`
    actual (prod):             `16842c40cefb0b5f54e4e02348d5ca10`
- `trading_corp/web/data.py`
    expected (origin/main LF): `11e942d320142d6965e4815032ad3c4e`
    actual (prod):             `d460fb143d08b31458bc0f73bd6cad1b`
- `trading_corp/web/routes.py`
    expected (origin/main LF): `cabfe8f7fc76e4b6fc8d56532f381ed4`
    actual (prod):             `936c7f4e476f783916f8869aa714d15a`
- `trading_corp/web/static/icons/apple-touch-icon-152.png`
    expected (origin/main LF): `2a37ed6626ef8cc2a089795957854616`
    actual (prod):             `fa0b8826a60f8b6029d5b5c8de785511`
- `trading_corp/web/static/icons/apple-touch-icon-167.png`
    expected (origin/main LF): `42ffc8107dd9f8ac9845f9fbeb21e5c9`
    actual (prod):             `06e1186d2a90658a9eba53384f61b6b3`
- `trading_corp/web/static/icons/apple-touch-icon-180.png`
    expected (origin/main LF): `6b8caca74143ea4d5f8bb0aabdc14754`
    actual (prod):             `04a7a0839bc11d50f00c7919fef0d504`
- `trading_corp/web/static/icons/favicon-16.png`
    expected (origin/main LF): `2fca3e6c10c185dcb839e9f9d2330a10`
    actual (prod):             `f78efaf1da3fea547d8f1d308a6e8fc3`
- `trading_corp/web/static/icons/favicon-32.png`
    expected (origin/main LF): `2d4b1a5f8c89d55eeba7bc7ac799d62b`
    actual (prod):             `66905635969bdd9a5d0e424c144a6eda`
- `trading_corp/web/static/icons/icon-192.png`
    expected (origin/main LF): `56afa020682480a4f944e3637a2a2b9f`
    actual (prod):             `6f01f0d099fec88a7ab89840e0356bb8`
- `trading_corp/web/static/icons/icon-512.png`
    expected (origin/main LF): `9b054ad21d2836232bd04a25a873cf5e`
    actual (prod):             `cfeefad9d2b57c81df995ea1a7aedb03`
- `trading_corp/web/static/icons/icon-maskable-512.png`
    expected (origin/main LF): `f2aca43f9ffa2e319f7aac32445a7435`
    actual (prod):             `6a4d8577f09498414c031d4506184d46`
- `trading_corp/web/templates/division.html`
    expected (origin/main LF): `ca894995be2cc8481a12e5a2e61b1d85`
    actual (prod):             `74937643e47ee9ed850d530309dd30c8`
- `trading_corp/web/templates/home.html`
    expected (origin/main LF): `617d4cca521ad8bf19d5a7157214d434`
    actual (prod):             `9834530b54872b42cf904180f4c9197e`
- `trading_corp/web/templates/iron_condor_live.html`
    expected (origin/main LF): `3cd5fbb0951517796c7804ae54916d35`
    actual (prod):             `810c4c120f0f537db21f600d6d1b32eb`
- `trading_corp/web/templates/partials/bitunix_score_panel.html`
    expected (origin/main LF): `77f19432fdec8a3c206788832abee029`
    actual (prod):             `62f085be373d9264d1eb69bf6a5d7ec8`
- `trading_corp/web/templates/partials/stat_cards.html`
    expected (origin/main LF): `90fa54a1a028fedb0b4b7323cd3db77d`
    actual (prod):             `1ad434b5bdc0e42a8a9579d925368077`
- `trading_corp/web/templates/partials/trade_flow.html`
    expected (origin/main LF): `c73e50ec12e90076096c032d8df828c4`
    actual (prod):             `db5805ec7ccc350a9b3e4b4c97199347`
- `trading_corp/web/templates/research.html`
    expected (origin/main LF): `c0cf78e199f43a2700dc35892499dad0`
    actual (prod):             `cbdef487ec4cfccd945a7e5653385e65`
- `trading_corp/web/webhooks.py`
    expected (origin/main LF): `ae1d1615c2fd457928ee5cc3d40a047c`
    actual (prod):             `86db1afec568a871b8a6e634c3b37a64`

## MISSING_ON_PROD (in main's tree, not on prod's disk)

These files were either never deployed, or were deleted on
prod. Investigate before next deploy.

- `trading_corp/agents/divisions/tasty_options.py`  (expected md5: `f994630177034cc9d1a8ba4cb9d4a0a0`)
- `trading_corp/agents/strategies/tasty_options_iron_condor.py`  (expected md5: `8babcd1ffb6b67c2f4147f78341970d3`)
- `trading_corp/brokers/bitunix_exceptions.py`  (expected md5: `4c78ebca522818c27c5acbe7806e8314`)
- `trading_corp/brokers/bitunix_symbols.py`  (expected md5: `aa7700822344c417fdbc46d80509988f`)
- `trading_corp/brokers/tastytrade.py`  (expected md5: `15fded375daa690fd2cde48e44c9e258`)
- `trading_corp/data/iem_cli_client.py`  (expected md5: `1c4d44ad1f2cfc02c79bb5b7c51f9df7`)
- `trading_corp/data/nbm_client.py`  (expected md5: `3429d292603ab3a2cc2887373d9b4503`)
- `trading_corp/data/residual_logic.py`  (expected md5: `84466f25b28827492daf451e565377e9`)
- `trading_corp/path_logger/__init__.py`  (expected md5: `a7806103b3d3272bd21c5d67c4147f81`)
- `trading_corp/path_logger/__main__.py`  (expected md5: `1e477f5c3cee61e7db4958c30299789d`)
- `trading_corp/path_logger/logger.py`  (expected md5: `75a13cdd032d16122100ef8ec6b5dffc`)
- `trading_corp/path_logger/main.py`  (expected md5: `cc4011031e18b52d71ed52b478a01b39`)
- `trading_corp/path_logger/store.py`  (expected md5: `884eb1eac7302e1393d9fe452e483f32`)
- `trading_corp/scripts/analyze_polymarket_whale.py`  (expected md5: `f8c459aa9ce944964bf3bcea4f567593`)

## PROD_ONLY_NOT_ON_MAIN (uncommitted prod additions)

These files exist on prod but are not git-tracked on
origin/main. May be uncommitted surgical edits — round-trip
to git or document as overlay.

- `config/Lets`
- `config/strategies.yaml.bak-day600-20260515-214835`
- `config/strategies.yaml.bak-h2-20260516T174505`
- `config/strategies.yaml.bak-h2-20260516T185125`
- `config/strategies.yaml.bak.2026-05-29-kalshi-disable`
- `config/strategies.yaml.orig`
- `trading_corp/agents/divisions/_observer_test.py`
- `trading_corp/agents/logger.py.bak-dblock-20260529`
- `trading_corp/agents/paper_trade_replay.py.bak-tgdiag-20260528`
- `trading_corp/agents/risk.py.bak-p2-scopeleak-20260515-222357`
- `trading_corp/agents/strategies/kalshi_copy_trader.py.bak-pre-e5efa06-20260528-044249`
- `trading_corp/agents/strategies/kalshi_crypto_arb.py.bak-fixd-20260516-005859`
- `trading_corp/agents/strategies/kalshi_weather_arb.py.bak-fixd-20260516-005859`
- `trading_corp/comms/bitunix_lifecycle_notifier.py.bak-phasec-20260529`
- `trading_corp/comms/bitunix_lifecycle_notifier.py.bak-tgdiag-20260528`
- `trading_corp/comms/telegram_bot.py.bak-phasec-20260529`
- `trading_corp/main.py.orig`
- `trading_corp/persistence/db.py.bak-dblock-20260529`

## Result: DRIFT DETECTED -- include stale-on-prod files in next deploy's transfer set; investigate missing/extra before proceeding.
