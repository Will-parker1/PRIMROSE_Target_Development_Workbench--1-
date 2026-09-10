#!/usr/bin/env bash
# Shared by the scripts in this folder. Not meant to be run directly.
set -euo pipefail

deployment_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$(cd "${deployment_dir}/.." && pwd)"

if [[ -f "${deployment_dir}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${deployment_dir}/.env"
  set +a
fi

: "${IMAGE_TAG:=dev}"
: "${IMAGE_PULL_POLICY:=Always}"
: "${K8S_NAMESPACE:=primrose}"
# ClusterIP needs no extra permissions and provisions no AWS resources —
# reach it with `kubectl port-forward`. Switch to NodePort/LoadBalancer only
# once you've confirmed you want (and can create) one of those.
: "${SERVICE_TYPE:=ClusterIP}"
: "${PVC_SIZE:=2Gi}"
# Empty = let the cluster's default StorageClass provision it (Minikube's
# hostpath default, or whatever a real cluster has marked default). Set
# explicitly (e.g. gp2/gp3) if the target cluster has no default class.
: "${STORAGE_CLASS:=}"

# Whether deploy.sh should even attempt each of these. Namespace, PVC,
# Secrets and the ServiceAccount patch are each a separate RBAC grant from
# "create a Deployment/Pod" — if you already know you don't have one of
# these, set it to false here to skip it (and the warning) entirely rather
# than finding out via a failed apply.
: "${MANAGE_NAMESPACE:=true}"
: "${MANAGE_PVC:=true}"
: "${MANAGE_SECRETS:=true}"
: "${MANAGE_SERVICE_ACCOUNT:=true}"

if [[ -z "${BACKEND_IMAGE:-}" || -z "${FRONTEND_IMAGE:-}" ]]; then
  if [[ -z "${REGISTRY:-}" ]]; then
    echo "Set REGISTRY in deployment/.env (copy deployment/.env.example), or set" >&2
    echo "BACKEND_IMAGE and FRONTEND_IMAGE directly." >&2
    exit 64
  fi
fi
: "${BACKEND_IMAGE:=${REGISTRY}/primrose-backend:${IMAGE_TAG}}"
: "${FRONTEND_IMAGE:=${REGISTRY}/primrose-frontend:${IMAGE_TAG}}"

export IMAGE_TAG IMAGE_PULL_POLICY K8S_NAMESPACE SERVICE_TYPE PVC_SIZE STORAGE_CLASS
export MANAGE_NAMESPACE MANAGE_PVC MANAGE_SECRETS MANAGE_SERVICE_ACCOUNT
export BACKEND_IMAGE FRONTEND_IMAGE
