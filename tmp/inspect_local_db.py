import sqlite3, sys

db = r"C:\Users\AA Incorporado\CC\data\trading_corp.db"
c = sqlite3.connect(db)
print(f"DB: {db}")
print("--- bitunix-relevant audit kind counts ---")
rows = c.execute("""
SELECT kind, COUNT(*) AS n
FROM audit_event
WHERE kind LIKE 'bitunix%' OR kind LIKE 'pa_%' OR kind LIKE 'htf_%'
   OR kind LIKE 'trade_plan%' OR kind LIKE 'position_sl%'
   OR kind LIKE 'would_have%' OR kind = 'webhook_received'
GROUP BY kind ORDER BY n DESC
""").fetchall()
for k, n in rows:
    print(f"  {k:40s} {n}")

print("--- max ts of bitunix_score_decided ---")
r = c.execute("SELECT MAX(ts), MIN(ts) FROM audit_event WHERE kind='bitunix_score_decided'").fetchone()
print(f"  max={r[0]}  min={r[1]}")

print("--- paper_trade_record bitunix_futures rows ---")
r = c.execute("SELECT COUNT(*), SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) FROM paper_trade_record WHERE division='bitunix_futures'").fetchone()
print(f"  total={r[0]}  resolved={r[1]}")
