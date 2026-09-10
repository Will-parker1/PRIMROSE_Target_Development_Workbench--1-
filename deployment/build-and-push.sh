#!/usr/bin/env bash
# Builds both images from the repo root and pushes them to REGISTRY.
# Run this wherever docker is available and already logged in to REGISTRY
# (`docker login`, or `aws ecr get-login-password | docker login --username
# AWS --password-stdin <registry>` for ECR). Configure via deployment/.env
# (copy deployment/.env.example) or exported environment variables.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${script_dir}/lib/env.sh"

echo "[build] backend  -> ${BACKEND_IMAGE}"
docker build -f "${repo_root}/Dockerfile.backend" -t "${BACKEND_IMAGE}" "${repo_root}"

echo "[build] frontend -> ${FRONTEND_IMAGE}"
docker build -f "${repo_root}/Dockerfile.frontend" -t "${FRONTEND_IMAGE}" "${repo_root}"

echo "[push] ${BACKEND_IMAGE}"
docker push "${BACKEND_IMAGE}"

echo "[push] ${FRONTEND_IMAGE}"
docker push "${FRONTEND_IMAGE}"

cat <<EOF

Pushed:
  ${BACKEND_IMAGE}
  ${FRONTEND_IMAGE}

From inside the KASM workspace (kubectl/minikube pointed at the target
cluster), with the same deployment/.env (or the same REGISTRY/IMAGE_TAG),
run:
  ./deploy.sh
EOF
