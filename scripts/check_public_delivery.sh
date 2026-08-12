#!/usr/bin/env bash
set -euo pipefail

mkdir -p artifacts/delivery
container="pulsyr-public-delivery-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"

cleanup() {
  docker logs "$container" > artifacts/delivery/caddy.log 2>&1 || true
  docker rm -f "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --detach --name "$container" \
  --publish 127.0.0.1:8080:8080 \
  --volume "$PWD/site/dist:/srv/pulsyr/site:ro" \
  --volume "$PWD/infra:/etc/caddy:ro" \
  caddy:2.10.2-alpine \
  caddy run --config /etc/caddy/Caddyfile.ci --adapter caddyfile >/dev/null

for _ in {1..20}; do
  if curl --fail --silent http://127.0.0.1:8080/ >/dev/null; then
    break
  fi
  sleep 1
done

curl --fail --silent --show-error \
  --header "Accept-Encoding: gzip" \
  --dump-header artifacts/delivery/home.headers \
  --output artifacts/delivery/home.html \
  http://127.0.0.1:8080/

asset_path="$(find site/dist/_astro -maxdepth 1 -type f \( -name '*.css' -o -name '*.js' \) | head -n 1)"
test -n "$asset_path"
asset_url="/${asset_path#site/dist/}"
curl --fail --silent --show-error \
  --header "Accept-Encoding: gzip" \
  --dump-header artifacts/delivery/asset.headers \
  --output /dev/null \
  "http://127.0.0.1:8080${asset_url}"

curl --silent --show-error \
  --dump-header artifacts/delivery/not-found.headers \
  --output artifacts/delivery/not-found.html \
  http://127.0.0.1:8080/definitely-missing

grep -Eiq '^HTTP/.* 200' artifacts/delivery/home.headers
grep -Eiq '^content-security-policy:' artifacts/delivery/home.headers
grep -Eiq '^x-frame-options: DENY' artifacts/delivery/home.headers
grep -Eiq '^x-content-type-options: nosniff' artifacts/delivery/home.headers
grep -Eiq '^cache-control: public, max-age=300, must-revalidate' artifacts/delivery/home.headers
grep -Eiq '^content-encoding: gzip' artifacts/delivery/home.headers
grep -Eiq '^cache-control: public, max-age=31536000, immutable' artifacts/delivery/asset.headers
grep -Eiq '^HTTP/.* 404' artifacts/delivery/not-found.headers
grep -Eiq '^content-security-policy:' artifacts/delivery/not-found.headers

printf '%s\n' \
  '{' \
  '  "gate": "delivery",' \
  '  "thresholds": {' \
  '    "security_headers_missing": 0,' \
  '    "immutable_assets_without_long_cache": 0,' \
  '    "html_without_revalidation": 0,' \
  '    "gzip_enabled": true,' \
  '    "branded_404_status": 404' \
  '  },' \
  '  "passed": true' \
  '}' > artifacts/delivery/summary.json

echo "Public delivery gate passed."
