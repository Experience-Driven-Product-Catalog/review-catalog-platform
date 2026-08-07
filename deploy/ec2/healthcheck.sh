#!/usr/bin/env sh
set -eu

curl --fail --silent --show-error "http://127.0.0.1:${API_PORT:-8000}/api/health"
curl --fail --silent --show-error "http://127.0.0.1:${WEB_PORT:-80}/" >/dev/null
