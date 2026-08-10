set -u
echo "=== listening sockets (python/uvicorn/http) ==="
ss -ltnp 2>/dev/null | grep -iE 'python|uvicorn|:8000|:8080|:80 |:5000|:3000|127.0.0.1' | head -20
echo "=== trading_corp service ExecStart ==="
systemctl cat trading_corp 2>/dev/null | grep -iE 'ExecStart|--port|--host|uvicorn|gunicorn' | head -10
echo "=== nginx proxy_pass (if any) ==="
grep -rhiE 'proxy_pass' /etc/nginx 2>/dev/null | head -5
echo "=== probe candidate ports for the app root ==="
for p in 8000 8080 8001 5000 3000 80; do code=$(curl -s -o /dev/null -m 3 -w "%{http_code}" http://127.0.0.1:$p/ 2>/dev/null); echo "port $p -> HTTP $code"; done
echo "=== probe a known polymarket GET route on 8000/8080 (discovery-control is GET) ==="
for p in 8000 8080; do code=$(curl -s -o /dev/null -m 3 -w "%{http_code}" http://127.0.0.1:$p/api/polymarket/discovery/control 2>/dev/null); echo "port $p /api/polymarket/discovery/control -> HTTP $code"; done
echo "=== DONE ==="
