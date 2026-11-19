#!/usr/bin/env bash
# order_db.dbml → order_db.sql (PostgreSQL). order_db.sql은 손으로 고치지 않는다.
set -euo pipefail
DBML_CLI_VERSION=12.1.0
cd "$(dirname "$0")/.."
docker run --rm -u "$(id -u):$(id -g)" -e npm_config_cache=/tmp/.npm \
  -v "$PWD:/w" -w /w node:20-alpine \
  npx --yes -p "@dbml/cli@${DBML_CLI_VERSION}" dbml2sql --postgres order_db.dbml -o order_db.sql
# 생성 시각 줄을 지워 다시 만들어도 같은 파일이 나오게 한다.
sed -i '/^-- Generated at:/d' order_db.sql
