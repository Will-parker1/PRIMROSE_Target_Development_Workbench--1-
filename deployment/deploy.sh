#!/usr/bin/env bash
# Applies the rendered manifests to whatever cluster `kubectl` currently
# points at. Run this from inside the KASM workspace (or wherever kubectl
# is already configured against the target cluster) after build-and-push.sh
# has pushed images for the same REGISTRY/IMAGE_TAG.
#
# Namespace, PVC, Secrets and the ServiceAccount patch are each a separate
# RBAC grant from "create a Deployment" — this script attempts each one but
# warns and continues rather than aborting if a step is forbidden, since you
# may not know in advance exactly what you're allowed to do. See the
# MANAGE_* toggles in .env.example to skip a step outright once you do know.
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${script_dir}/lib/env.sh"
# lib/env.sh sets -e for its own validation logic; this script instead
# checks each privileged step's exit status itself, so turn -e back off
# now that sourcing is done (pipefail and nounset stay on).
set +e

FATAL=0

note()  { echo "[deploy] $*"; }
warn()  { echo "[deploy] WARNING: $*" >&2; }

echo "[deploy] context:   $(kubectl config current-context 2>/dev/null || echo 'unknown')"
echo "[deploy] namespace: ${K8S_NAMESPACE}"

if ! "${script_dir}/render-manifests.sh"; then
  echo "[deploy] rendering manifests failed — see above. Nothing was applied." >&2
  exit 1
fi
rendered="${script_dir}/manifests/rendered"

# --- Namespace -------------------------------------------------------------
if [[ "${MANAGE_NAMESPACE}" == "true" ]]; then
  if kubectl apply -f "${rendered}/namespace.yaml"; then
    status_namespace="ok"
  else
    status_namespace="FAILED (likely no permission to manage Namespace objects)"
    warn "could not apply the namespace — continuing on the assumption '${K8S_NAMESPACE}' already exists"
  fi
else
  status_namespace="skipped (MANAGE_NAMESPACE=false)"
fi

# --- Optional registry pull secret -----------------------------------------
if [[ -n "${REGISTRY_USERNAME:-}" && -n "${REGISTRY_PASSWORD:-}" ]]; then
  if [[ "${MANAGE_SERVICE_ACCOUNT}" == "true" ]]; then
    : "${REGISTRY:?REGISTRY must be set to create an image pull secret}"
    note "creating/updating image pull secret for ${REGISTRY}"
    if kubectl create secret docker-registry primrose-registry-credentials \
        --namespace "${K8S_NAMESPACE}" \
        --docker-server="${REGISTRY}" \
        --docker-username="${REGISTRY_USERNAME}" \
        --docker-password="${REGISTRY_PASSWORD}" \
        --dry-run=client -o yaml | kubectl apply -f -; then
      if kubectl patch serviceaccount default -n "${K8S_NAMESPACE}" \
          -p '{"imagePullSecrets": [{"name": "primrose-registry-credentials"}]}'; then
        status_pull_secret="ok"
      else
        status_pull_secret="FAILED (secret created, but could not patch the default ServiceAccount)"
        warn "created primrose-registry-credentials but couldn't attach it to the default ServiceAccount"
      fi
    else
      status_pull_secret="FAILED (likely no permission to create Secrets)"
      warn "could not create the registry pull secret"
    fi
  else
    status_pull_secret="skipped (MANAGE_SERVICE_ACCOUNT=false)"
  fi
else
  status_pull_secret="not requested (REGISTRY_USERNAME/REGISTRY_PASSWORD unset)"
fi

# --- Backend config secret ---------------------------------------------------
backend_env_file="${repo_root}/deploy/backend.env"
if [[ "${MANAGE_SECRETS}" == "true" ]]; then
  if [[ -f "${backend_env_file}" ]]; then
    note "creating/updating primrose-backend-env secret from deploy/backend.env"
    if kubectl create secret generic primrose-backend-env \
        --namespace "${K8S_NAMESPACE}" \
        --from-env-file="${backend_env_file}" \
        --dry-run=client -o yaml | kubectl apply -f -; then
      status_backend_secret="ok"
    else
      status_backend_secret="FAILED (likely no permission to create Secrets)"
      warn "could not create primrose-backend-env — the Pod will not start without it"
    fi
  else
    status_backend_secret="FAILED (deploy/backend.env not found)"
    warn "${backend_env_file} not found — copy deploy/backend.env.example, set"
    warn "PRIMROSE_BACKEND_TOKEN, and re-run. The Pod will not start without it."
  fi
else
  status_backend_secret="skipped (MANAGE_SECRETS=false)"
fi

# --- PVC ---------------------------------------------------------------------
if [[ "${MANAGE_PVC}" == "true" ]]; then
  if kubectl apply -f "${rendered}/pvc.yaml"; then
    status_pvc="ok"
  else
    status_pvc="FAILED (likely no permission to create PersistentVolumeClaims)"
    warn "could not apply the PVC — continuing on the assumption 'primrose-data' already exists"
  fi
else
  status_pvc="skipped (MANAGE_PVC=false)"
fi

# --- Deployment + Service (the part you've confirmed you can do) -----------
if kubectl apply -f "${rendered}/deployment.yaml"; then
  status_deployment="ok"
else
  status_deployment="FAILED"
  FATAL=1
fi

if kubectl apply -f "${rendered}/service.yaml"; then
  status_service="ok"
else
  status_service="FAILED (likely no permission to create Services)"
  warn "could not apply the Service — the Deployment may still be reachable via"
  warn "'kubectl port-forward pod/<name> ...' once it's running"
fi

if [[ "${FATAL}" -eq 0 ]]; then
  if kubectl -n "${K8S_NAMESPACE}" rollout status deployment/primrose --timeout=180s; then
    status_rollout="ok"
  else
    status_rollout="did not become ready in time"
    warn "rollout didn't complete — if any step above failed or was skipped,"
    warn "that's likely why. Check: kubectl -n ${K8S_NAMESPACE} describe pod -l app=primrose"
  fi
else
  status_rollout="skipped (Deployment apply failed)"
fi

echo
echo "=== Summary ==="
printf '  %-15s %s\n' "namespace:" "${status_namespace}"
printf '  %-15s %s\n' "pull_secret:" "${status_pull_secret}"
printf '  %-15s %s\n' "backend_secret:" "${status_backend_secret}"
printf '  %-15s %s\n' "pvc:" "${status_pvc}"
printf '  %-15s %s\n' "deployment:" "${status_deployment}"
printf '  %-15s %s\n' "service:" "${status_service}"
printf '  %-15s %s\n' "rollout:" "${status_rollout}"
echo

if [[ "${FATAL}" -eq 1 ]]; then
  echo "The Deployment itself failed to apply — nothing is running. See the error above." >&2
  exit 1
fi

cat <<EOF
backend:  ${BACKEND_IMAGE}
frontend: ${FRONTEND_IMAGE}

Reach it with:
  kubectl -n ${K8S_NAMESPACE} port-forward svc/primrose 8080:3000
  (or, if the Service failed above: kubectl -n ${K8S_NAMESPACE} port-forward deploy/primrose 8080:3000)
EOF
