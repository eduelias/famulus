#!/usr/bin/env sh
# One-time setup: create .env from the example and generate a SearXNG secret.
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env — edit it with your WhatsApp credentials"
fi

mkdir -p data searxng
if [ ! -f searxng/settings.yml ]; then
  SECRET=$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null \
        || openssl rand -hex 16)
  cat > searxng/settings.yml <<EOF
use_default_settings: true

server:
  secret_key: "$SECRET"
  limiter: false

search:
  formats:
    - html
    - json
EOF
  echo "generated searxng/settings.yml with a fresh secret key"
fi

echo "setup done. Next: edit .env, then: docker compose up -d --build"
