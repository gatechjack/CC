#!/usr/bin/env bash
# Piece 1 two-state collapse — APPLY (no restart). Streamed to prod via p1_apply.ps1.
# Aborts (set -e) on any md5 mismatch: staged-transfer integrity, pre-deploy live
# version match (catches prod drift since drift-gate), and placed == staged.
set -e
ROOT=/home/azureuser/trading_corp
ST=/home/azureuser/p1_staged
BAK=/home/azureuser/p1_bak_2026-06-27

echo "== 1/5 verify staged transfer integrity (LF md5) =="
echo "698cd083d484296ac6f991224fdac376  $ST/main.py" | md5sum -c -
echo "dd64a7f4f6a16ed7cf9c2051f612fc31  $ST/bitunix_futures_observer.py" | md5sum -c -
echo "0cd6e45d758c8e6d226302d4055bce44  $ST/strategies.yaml" | md5sum -c -

echo "== 2/5 verify LIVE files are the expected pre-deploy versions (drift guard) =="
echo "1069a6db98da8cffbf34bb8f365bc4e6  $ROOT/trading_corp/main.py" | md5sum -c -
echo "2647fccc630c8acacbe0d5a32f05b1c8  $ROOT/trading_corp/agents/divisions/bitunix_futures_observer.py" | md5sum -c -
echo "281b373f033dbcf23fc0176372470e1e  $ROOT/config/strategies.yaml" | md5sum -c -

echo "== 3/5 backup live -> $BAK =="
mkdir -p "$BAK"
cp -p "$ROOT/trading_corp/main.py" "$BAK/main.py.bak-pre-twostate-2026-06-27"
cp -p "$ROOT/trading_corp/agents/divisions/bitunix_futures_observer.py" "$BAK/bitunix_futures_observer.py.bak-pre-twostate-2026-06-27"
cp -p "$ROOT/config/strategies.yaml" "$BAK/strategies.yaml.bak-pre-twostate-2026-06-27"

echo "== 4/5 place staged -> live =="
cp "$ST/main.py" "$ROOT/trading_corp/main.py"
cp "$ST/bitunix_futures_observer.py" "$ROOT/trading_corp/agents/divisions/bitunix_futures_observer.py"
cp "$ST/strategies.yaml" "$ROOT/config/strategies.yaml"

echo "== 5/5 verify PLACED md5 == staged =="
echo "698cd083d484296ac6f991224fdac376  $ROOT/trading_corp/main.py" | md5sum -c -
echo "dd64a7f4f6a16ed7cf9c2051f612fc31  $ROOT/trading_corp/agents/divisions/bitunix_futures_observer.py" | md5sum -c -
echo "0cd6e45d758c8e6d226302d4055bce44  $ROOT/config/strategies.yaml" | md5sum -c -

echo "== byte-unchanged proof: SFP/recon must be UNTOUCHED (18da45f2 / 5c71a103 / 3a23610c) =="
md5sum "$ROOT/trading_corp/agents/divisions/bitunix_sfp_observer.py" \
       "$ROOT/trading_corp/agents/strategies/bitunix_sfp.py" \
       "$ROOT/trading_corp/agents/divisions/bitunix_position_reconciler.py"

echo
echo "APPLY OK. Engine NOT restarted. Review above, then run:  powershell -ep bypass -f .\\p1_restart.ps1"
echo "ROLLBACK (if needed): cp $BAK/*.bak-* back over the live paths, then restart."
