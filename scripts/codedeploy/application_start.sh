#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=scripts/codedeploy/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

test -x "${release_dir}/deploy/aws/activate-release.sh"
DEPLOYMENT_REVISION="${revision}" \
  "${release_dir}/deploy/aws/activate-release.sh" "${release_dir}"
