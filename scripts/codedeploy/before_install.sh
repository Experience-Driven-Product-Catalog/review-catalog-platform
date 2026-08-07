#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=scripts/codedeploy/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

install -d -m 0755 \
  "${application_root}/release-staging" \
  "${application_root}/releases" \
  "${application_root}/shared"

if [[ ! -d "${release_dir}" ]]; then
  rm -rf -- "${stage_dir}"
  install -d -m 0755 "${stage_dir}"
fi
