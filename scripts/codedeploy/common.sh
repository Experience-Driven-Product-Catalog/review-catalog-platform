#!/usr/bin/env bash
set -euo pipefail

bundle_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
revision="$(tr -d '\n' < "${bundle_root}/REVISION")"

if [[ ! "${revision}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "REVISION must contain one full git SHA" >&2
  exit 2
fi

application_root="/opt/review-catalog-platform"
release_name="git-${revision}"
stage_dir="${application_root}/release-staging/${release_name}"
release_dir="${application_root}/releases/${release_name}"
shared_models="${application_root}/shared/models"

export bundle_root revision application_root release_name stage_dir release_dir shared_models
