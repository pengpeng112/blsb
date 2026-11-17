#!/bin/sh
# usage: docker exec <worker-container> /data/exchange/inf-pusher/run.sh <table|all|reconcile> <mock|real> [limit|yesterday|YYYY-MM-DD]
cd /data/exchange/inf-pusher
if [ -f ./inf.env ]; then . ./inf.env; export INF_ODS_USER INF_ODS_PASSWORD INF_ODS_DSN; fi
if [ "$1" = "reconcile" ]; then
  # 日终对账：第二参数为目标日（yesterday 默认 / YYYY-MM-DD），mode 固定 real（幂等补推）
  exec python pusher.py --config config.json --table reconcile --mode real ${2:+--reconcile-date "$2"}
fi
exec python pusher.py --config config.json --table "$1" --mode "$2" ${3:+--limit $3}
