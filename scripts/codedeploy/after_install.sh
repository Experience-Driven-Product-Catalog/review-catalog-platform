#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=scripts/codedeploy/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [[ -d "${release_dir}" ]]; then
  exit 0
fi

if [[ ! -d "${shared_models}" ]]; then
  source_models="${application_root}/releases/0.3.0/models"
  if [[ ! -d "${source_models}" ]]; then
    echo "bootstrap embedding model is missing: ${source_models}" >&2
    exit 1
  fi
  cp -a "${source_models}" "${shared_models}"
  chmod -R a+rX "${shared_models}"
fi

cp -a "${bundle_root}/." "${stage_dir}/"
rm -rf -- "${stage_dir}/models"
ln -s "${shared_models}" "${stage_dir}/models"
mv "${stage_dir}" "${release_dir}"
