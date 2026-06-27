#!/usr/bin/env bash
# SFP DISARM — flip bitunix_sfp.auto_execute true->false (HOT, no restart).
# Streamed via sfp_disarm.ps1. Aborts on any md5 mismatch.
set -e
ROOT=/home/azureuser/trading_corp
F=$ROOT/config/strategies.yaml
ST=/home/azureuser/sfp_disarm_staged/strategies.yaml
BAK=/home/azureuser/p1_bak_2026-06-27

echo "== verify staged transfer integrity =="
echo "649070f5915d7ed35a29fd5900acb5bc  $ST" | md5sum -c -

echo "== verify LIVE strategies.yaml is the current Piece-1 version (drift guard) =="
echo "0cd6e45d758c8e6d226302d4055bce44  $F" | md5sum -c -

echo "== backup + place (HOT: auto_execute is mtime-reloaded per placement; NO restart) =="
mkdir -p "$BAK"
cp -p "$F" "$BAK/strategies.yaml.bak-pre-disarm-2026-06-27"
cp "$ST" "$F"

echo "== verify placed (649070f5) =="
echo "649070f5915d7ed35a29fd5900acb5bc  $F" | md5sum -c -

echo "== confirm bitunix_sfp.auto_execute is now false =="
awk '/^bitunix_sfp:/{f=1} f&&/^  auto_execute:/{print; exit}' "$F"

echo
echo "DISARM OK (hot, no restart). SFP keeps detecting 15m signals but will NOT place."
echo "RE-ARM later: restore auto_execute: true (after TP-fix deploy + validate)."
