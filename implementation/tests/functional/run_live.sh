#!/usr/bin/env bash
# 真实栈功能测试执行器：每次都从干净状态开始（栈重启+独立测试库），
# 避免"上一轮残留的CONFIRMED/已发布状态"污染断言（功能测试踩过的坑）。
set -e
cd "$(dirname "$0")/../.."   # implementation/

for port in 8000 8001 8002 8090; do
  P=$(netstat -ano | grep ":$port" | grep LISTEN | awk '{print $5}' | head -1)
  if [ -n "$P" ]; then taskkill //PID "$P" //F >/dev/null 2>&1 || true; fi
done
sleep 1
rm -f var/functest/core.sqlite3* var/functest/exchange.sqlite3* var/functest/agent-queue.sqlite3*
rm -rf var/functest/scanner-output var/functest/volume1

BLYB_DATABASE_URL="sqlite:///var/functest/core.sqlite3" \
BLYB_EXCHANGE_DB="$(pwd)/var/functest/exchange.sqlite3" \
  nohup python run_dev.py > var/functest/run_dev.log 2>&1 &

for i in $(seq 1 30); do
  if curl -s -m 2 http://127.0.0.1:8090/health >/dev/null 2>&1; then break; fi
  sleep 1
done

BLYB_LIVE_STACK=1 python -m pytest tests/functional/test_live_stack.py -q -s --tb=short "$@"
