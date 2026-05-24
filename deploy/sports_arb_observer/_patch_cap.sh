/home/azureuser/trading_corp/venv/bin/python3 - <<'PY'
yaml_path = "/home/azureuser/trading_corp/config/strategies.yaml"
src = open(yaml_path, encoding='utf-8').read()
obs_marker = 'kalshi_sports_arb_observer:'
next_marker = 'kalshi_copy_trader:'
start = src.find(obs_marker)
end = src.find(next_marker, start)
if start < 0 or end < 0:
    print(f"MARKER NOT FOUND: start={start} end={end}")
    raise SystemExit(1)
block = src[start:end]
old = 'max_markets_per_series: 50'
new = 'max_markets_per_series: 150'
if old not in block:
    print(f"OLD VALUE NOT IN BLOCK; aborting. Block snippet:")
    print(block[:400])
    raise SystemExit(1)
if block.count(old) != 1:
    print(f"EXPECTED 1 OCCURRENCE in block, FOUND {block.count(old)}; aborting")
    raise SystemExit(1)
new_block = block.replace(old, new, 1)
src_new = src[:start] + new_block + src[end:]
open(yaml_path, 'w', encoding='utf-8').write(src_new)
# Verify
src_check = open(yaml_path, encoding='utf-8').read()
block_check = src_check[src_check.find(obs_marker):src_check.find(next_marker, src_check.find(obs_marker))]
print("POST-PATCH OBSERVER DISCOVERY BLOCK:")
in_disc = False
for line in block_check.split('\n'):
    s = line.strip()
    if s.startswith('discovery:'):
        in_disc = True
        print(f"  {line}")
        continue
    if in_disc and (s == '' or not (line.startswith('    ') or line.startswith('  #'))):
        in_disc = False
    if in_disc:
        print(f"  {line}")
PY
