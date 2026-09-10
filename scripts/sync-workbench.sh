#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/.." && pwd)"
source_dir="${project_root}/backend/web"
target_dir="${project_root}/public/workbench"

for asset in index.html app.js styles.css; do
  [[ -f "${source_dir}/${asset}" ]] || {
    echo "Missing authoritative workbench asset: backend/web/${asset}" >&2
    exit 66
  }
done

mkdir -p "${target_dir}"
cp "${source_dir}/index.html" "${target_dir}/index.html"
cp "${source_dir}/app.js" "${target_dir}/app.js"
cp "${source_dir}/styles.css" "${target_dir}/styles.css"

echo "Synced authoritative Python workbench UI to public/workbench."
