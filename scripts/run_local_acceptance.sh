#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

mysql_bin=/usr/local/mysql/bin
data_dir="/private/tmp/cvbuilder-local-acceptance-${UID}"
socket="$data_dir/mysql.sock"
database=cvbuilder_acceptance_test

if [[ ! -x "$mysql_bin/mysqld" ]]; then
  echo "未找到 MySQL 8.4：$mysql_bin/mysqld" >&2
  exit 1
fi
if [[ ! -x .venv/bin/cvbuilder-web ]]; then
  echo "未找到 .venv/bin/cvbuilder-web；请先安装项目依赖。" >&2
  exit 1
fi

mkdir -p "$data_dir"
chmod 700 "$data_dir"
if [[ ! -d "$data_dir/mysql" ]]; then
  "$mysql_bin/mysqld" --initialize-insecure --datadir="$data_dir" --log-error="$data_dir/mysql.err"
fi
if "$mysql_bin/mysqladmin" --socket="$socket" -uroot ping >/dev/null 2>&1; then
  echo "本地验收 MySQL 已在运行；请使用原来的终端。" >&2
  exit 1
fi

"$mysql_bin/mysqld" --datadir="$data_dir" --socket="$socket" \
  --pid-file="$data_dir/mysql.pid" --log-error="$data_dir/mysql.err" \
  --skip-networking --mysqlx=OFF &
mysql_pid=$!
web_pid=
cleanup() {
  if [[ -n "$web_pid" ]]; then
    kill "$web_pid" 2>/dev/null || true
  fi
  "$mysql_bin/mysqladmin" --socket="$socket" -uroot shutdown >/dev/null 2>&1 || true
  wait "$mysql_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ready=0
for _ in {1..50}; do
  if "$mysql_bin/mysqladmin" --socket="$socket" -uroot ping >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$mysql_pid" 2>/dev/null; then
    break
  fi
  sleep 0.2
done
if [[ "$ready" -ne 1 ]]; then
  echo "本地 MySQL 启动失败；详情见 $data_dir/mysql.err" >&2
  exit 1
fi

"$mysql_bin/mysql" --socket="$socket" -uroot \
  -e "CREATE DATABASE IF NOT EXISTS $database CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
export CV_MYSQL_DATABASE="$database"
export CV_MYSQL_USER=root
export CV_MYSQL_SOCKET="$socket"
unset CV_MYSQL_HOST CV_MYSQL_PORT CV_MYSQL_PASSWORD CV_MYSQL_SECRET_ARN
.venv/bin/python -m cvbuilder.mysql_migrate

echo "本地验收网页：http://127.0.0.1:8000（按 Ctrl+C 停止）"
.venv/bin/cvbuilder-web &
web_pid=$!
wait "$web_pid"
