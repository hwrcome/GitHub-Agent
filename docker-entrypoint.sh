#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  alembic upgrade head
fi

exec "$@"
