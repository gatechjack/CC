# Path Logger Isolation Baseline

Operator runbook for capturing pre-deploy and post-deploy isolation numbers
before and after starting `trading-corp-path-logger.service`. Fill in the
numeric fields below after each capture run.

---

## Pre-deploy capture commands

Run these **before** starting the path logger service. Record the results
in the "Pre-deploy numbers" section below.

```bash
# 1. Strategy CPU — 60-second sample, 1Hz
top -b -n 60 -d 1 -p $(pgrep -f 'python -X utf8 -m trading_corp$') \
  | tee /tmp/pre_deploy_top.txt

# 2. Disk IO — 60-second sample, 1Hz
iostat -x 1 60 | tee /tmp/pre_deploy_iostat.txt

# 3. Baseline warning/error count in trading-corp.service (last 24h)
journalctl -u trading-corp.service --since "24 hours ago" -p warning | wc -l
```

---

## Post-deploy capture commands

Run these **24 hours after** starting the path logger service. Record the
results in the "Post-deploy numbers" section below.

```bash
# 1. Strategy CPU — 60-second sample, 1Hz (same command as pre-deploy)
top -b -n 60 -d 1 -p $(pgrep -f 'python -X utf8 -m trading_corp$') \
  | tee /tmp/post_deploy_top.txt

# 2. Disk IO — 60-second sample, 1Hz
iostat -x 1 60 | tee /tmp/post_deploy_iostat.txt

# 3. Post-deploy warning/error count in trading-corp.service (last 24h)
journalctl -u trading-corp.service --since "24 hours ago" -p warning | wc -l

# 4. Path logger health check — confirm heartbeat rows present
sqlite3 data/path_logger.db \
  "SELECT event_type, COUNT(*) AS n, MAX(captured_ts) AS last_seen_ms
   FROM logger_jitter
   GROUP BY event_type
   ORDER BY n DESC;"

# 5. p95 jitter check
sqlite3 data/path_logger.db \
  "SELECT event_type, ticker, json_extract(payload_json, '$.p95_gap_ms') AS p95
   FROM logger_jitter
   WHERE event_type = 'jitter_report'
     AND captured_ts > (strftime('%s','now') - 86400) * 1000
   ORDER BY p95 DESC LIMIT 20;"
```

---

## Pass criteria

| Metric | Threshold |
|---|---|
| Strategy CPU mean (pre vs post) | Within +5% of pre-deploy baseline |
| New ERROR/WARNING in trading-corp.service | Zero new entries within 24h post-deploy |
| p95 jitter (dense windows) | ≤ 3000 ms |
| p95 jitter (heartbeat window) | ≤ 90000 ms |
| Heartbeat rows present | At least one row per 65s in logger_jitter |

---

## Pre-deploy numbers

**Capture date/time (UTC):**

**Strategy CPU (top output — mean %CPU column):**

```
[paste top summary here]
```

**Disk IO (iostat — %util for data disk):**

```
[paste iostat summary here]
```

**trading-corp.service warning count (last 24h):**

```
[paste wc -l output here]
```

---

## Post-deploy numbers

**Capture date/time (UTC):**

**Strategy CPU (top output — mean %CPU column):**

```
[paste top summary here]
```

**Disk IO (iostat — %util for data disk):**

```
[paste iostat summary here]
```

**trading-corp.service warning count (last 24h):**

```
[paste wc -l output here]
```

**Path logger jitter summary:**

```
[paste sqlite3 jitter query output here]
```

---

## Verdict

**CPU delta (post - pre):**

**New trading-corp.service warnings:**

**p95 jitter (dense):**

**p95 jitter (heartbeat):**

**PASS / FAIL:**

**Notes:**
