#!/bin/bash
set -euo pipefail

release_dir="${1:?release directory is required}"
deployment_revision="${DEPLOYMENT_REVISION:-local}"
shared_dir="/opt/review-catalog-platform/shared"
env_file="${shared_dir}/.env"

install -d -m 0755 "${shared_dir}"

if [[ ! -f "${env_file}" ]]; then
  umask 077
  postgres_password="$(openssl rand -hex 24)"
  airflow_password="$(openssl rand -hex 18)"
  airflow_fernet_key="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  airflow_jwt_secret="$(openssl rand -hex 32)"

  printf '%s\n' \
    "POSTGRES_PASSWORD=${postgres_password}" \
    "AIRFLOW_USERNAME=airflow" \
    "AIRFLOW_PASSWORD=${airflow_password}" \
    "AIRFLOW_UID=50000" \
    "AIRFLOW_FERNET_KEY=${airflow_fernet_key}" \
    "AIRFLOW_JWT_SECRET=${airflow_jwt_secret}" \
    "EXTRACTION_BACKEND=codex_cli" \
    "EXTRACTION_MODEL=gpt-5.6-luna" \
    "EXTRACTION_REASONING_EFFORT=high" \
    "REVIEW_CATALOG_EXTRAS=[embedding,reclustering]" \
    "REVIEW_CATALOG_VERSION=0.4.0" \
    "CODEX_CLI_VERSION=0.146.0" \
    "WEB_BIND_ADDRESS=0.0.0.0" \
    "WEB_PORT=80" \
    "API_BIND_ADDRESS=127.0.0.1" \
    "AIRFLOW_BIND_ADDRESS=127.0.0.1" \
    "OPENAI_API_KEY=" > "${env_file}"
  chmod 0600 "${env_file}"
fi

# These non-secret build settings are part of the application release contract.
# Existing hosts keep their generated secrets while receiving dependency/version upgrades.
if grep -q '^REVIEW_CATALOG_EXTRAS=' "${env_file}"; then
  sed -i 's/^REVIEW_CATALOG_EXTRAS=.*/REVIEW_CATALOG_EXTRAS=[embedding,reclustering]/' "${env_file}"
else
  printf '%s\n' 'REVIEW_CATALOG_EXTRAS=[embedding,reclustering]' >> "${env_file}"
fi
if grep -q '^REVIEW_CATALOG_VERSION=' "${env_file}"; then
  sed -i 's/^REVIEW_CATALOG_VERSION=.*/REVIEW_CATALOG_VERSION=0.4.0/' "${env_file}"
else
  printf '%s\n' 'REVIEW_CATALOG_VERSION=0.4.0' >> "${env_file}"
fi

ln -sfn "${env_file}" "${release_dir}/.env"
for application_input in airflow config ingestion migration models; do
  input_path="${release_dir}/${application_input}"
  if [[ ! -e "${input_path}" ]]; then
    echo "required application input is missing: ${input_path}" >&2
    exit 1
  fi
  if [[ -L "${input_path}" ]]; then
    chmod -R a+rX "$(readlink -f "${input_path}")"
  else
    chmod -R a+rX "${input_path}"
  fi
done
cd "${release_dir}"

DEPLOYMENT_REVISION="${deployment_revision}" \
  docker compose --project-name review-catalog-platform build airflow-scheduler api frontend
DEPLOYMENT_REVISION="${deployment_revision}" \
  docker compose --project-name review-catalog-platform up -d

# Nginx resolves the Docker DNS name for the API when it starts. A source-only
# deployment can recreate the API while leaving an unchanged frontend image
# running with the previous API container IP, so always refresh this proxy
# after the dependency-gated API startup has completed.
DEPLOYMENT_REVISION="${deployment_revision}" \
  docker compose --project-name review-catalog-platform up -d --no-deps --force-recreate frontend

docker compose --project-name review-catalog-platform ps
