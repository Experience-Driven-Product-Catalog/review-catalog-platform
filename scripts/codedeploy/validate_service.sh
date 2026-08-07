#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=scripts/codedeploy/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

for attempt in $(seq 1 60); do
  if curl --fail --silent http://127.0.0.1/api/health >/dev/null; then
    break
  fi
  if [[ "${attempt}" -eq 60 ]]; then
    echo "API health check did not recover" >&2
    exit 1
  fi
  sleep 2
done

index_html="$(curl --fail --silent http://127.0.0.1/)"
asset_path="$(printf '%s' "${index_html}" | grep -o 'src="/assets/[^"]*\.js"' | head -1 | cut -d '"' -f2)"
test -n "${asset_path}"
curl --fail --silent "http://127.0.0.1${asset_path}" | grep -F 'CODEDEPLOY' >/dev/null

cd "${release_dir}"
docker compose --project-name review-catalog-platform exec -T airflow-scheduler codex login status
ln -sfn "${release_dir}" "${application_root}/current"
docker compose --project-name review-catalog-platform ps
