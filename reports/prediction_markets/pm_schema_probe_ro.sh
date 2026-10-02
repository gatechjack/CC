cd /home/azureuser/trading_corp || exit 2
echo "=== DB files present ==="
ls -la data/trading_corp.db data/prediction_markets.db 2>&1 | awk '{print $NF, $5}'
echo "=== trading_corp.db: migration/schema/version tables ==="
sqlite3 -readonly data/trading_corp.db "SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%migrat%' OR name LIKE '%schema%' OR name LIKE '%version%');" 2>&1
echo "=== prediction_markets.db: migration/schema/version tables ==="
sqlite3 -readonly data/prediction_markets.db "SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%migrat%' OR name LIKE '%schema%' OR name LIKE '%version%');" 2>&1
echo "=== heads (try common names on BOTH dbs) ==="
for db in data/trading_corp.db data/prediction_markets.db; do for t in schema_version schema_migrations migrations _migrations alembic_version; do v=$(sqlite3 -readonly "$db" "SELECT MAX(version) FROM $t;" 2>/dev/null); [ -n "$v" ] && echo "$db.$t MAX(version)=$v"; vn=$(sqlite3 -readonly "$db" "SELECT COUNT(*) FROM $t;" 2>/dev/null); [ -n "$vn" ] && echo "  ($db.$t rows=$vn)"; done; done
echo "DONE_SCHEMA"
