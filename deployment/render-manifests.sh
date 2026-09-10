#!/usr/bin/env bash
# Renders deployment/manifests/*.yaml.tmpl into deployment/manifests/rendered/
# using the values resolved by lib/env.sh. Safe to run standalone to review
# what deploy.sh would apply.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${script_dir}/lib/env.sh"

command -v envsubst >/dev/null || {
  echo "envsubst is required (gettext package). Install it, e.g.:" >&2
  echo "  Debian/Ubuntu: apt-get install gettext-base" >&2
  echo "  macOS:         brew install gettext" >&2
  exit 69
}

out_dir="${script_dir}/manifests/rendered"
mkdir -p "${out_dir}"

vars='${K8S_NAMESPACE} ${PVC_SIZE} ${SERVICE_TYPE} ${BACKEND_IMAGE} ${FRONTEND_IMAGE} ${IMAGE_PULL_POLICY}'

for tmpl in "${script_dir}"/manifests/*.yaml.tmpl; do
  name="$(basename "${tmpl}" .tmpl)"
  envsubst "${vars}" < "${tmpl}" > "${out_dir}/${name}"
  echo "[render] ${name}"
done

# storageClassName is deliberately not in the template: an empty string
# there means "use no StorageClass" (static provisioning only), which is
# different from omitting the field (defer to the cluster's default class).
# Only add the line at all when STORAGE_CLASS is actually set.
if [[ -n "${STORAGE_CLASS}" ]]; then
  printf '  storageClassName: %s\n' "${STORAGE_CLASS}" >> "${out_dir}/pvc.yaml"
  echo "[render] pvc.yaml: pinned storageClassName=${STORAGE_CLASS}"
fi

echo "Rendered into ${out_dir}"
