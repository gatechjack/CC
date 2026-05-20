#!/bin/bash
BASE=/home/azureuser/trading_corp
for f in trading_corp/web/routes.py trading_corp/web/data.py; do
  prod_md5=$(md5sum "$BASE/$f" 2>/dev/null | awk '{print $1}')
  echo "$f prod=$prod_md5"
done
echo ""
echo "===== Existing backup tags ====="
ls -1 $BASE/trading_corp/web/routes.py.* $BASE/trading_corp/web/data.py.* 2>/dev/null | head -20
