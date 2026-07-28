#!/bin/sh
set -e

# O .db é montado read-only: rodar migrations aqui faria o container falhar na
# subida. Só migra quando explicitamente pedido (RUN_MIGRATIONS=1).
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "Running migrations..."
  python -m alembic -c alembic.ini upgrade head
fi

echo "Starting API..."
exec python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
