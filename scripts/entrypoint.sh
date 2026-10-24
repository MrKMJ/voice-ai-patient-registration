#!/bin/sh
# Start as root only long enough to fix ownership of mounted volumes (Fly volumes are
# root-owned), then drop privileges and run the server as an unprivileged user.
set -e
for dir in /data /app/data; do
  [ -d "$dir" ] && chown -R appuser:appuser "$dir"
done
exec setpriv --reuid=appuser --regid=appuser --init-groups \
  uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips='*'
