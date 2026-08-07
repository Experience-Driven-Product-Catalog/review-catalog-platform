#!/bin/bash
set -euxo pipefail

dnf install -y docker jq tar gzip ruby wget
systemctl enable --now docker
systemctl enable --now amazon-ssm-agent

install -d -m 0755 /usr/local/lib/docker/cli-plugins
curl --fail --location --silent --show-error \
  https://github.com/docker/compose/releases/download/v2.39.4/docker-compose-linux-aarch64 \
  --output /usr/local/lib/docker/cli-plugins/docker-compose
chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose

install -d -o ec2-user -g ec2-user -m 0755 /opt/review-catalog-platform

codedeploy_installer="/tmp/codedeploy-install"
curl --fail --location --silent --show-error \
  https://aws-codedeploy-ap-northeast-2.s3.ap-northeast-2.amazonaws.com/latest/install \
  --output "${codedeploy_installer}"
chmod 0755 "${codedeploy_installer}"
"${codedeploy_installer}" auto
systemctl enable --now codedeploy-agent

docker --version
docker compose version
systemctl is-active codedeploy-agent
