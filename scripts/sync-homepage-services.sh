#!/usr/bin/env bash
# Sync Homepage services.yaml from Docker containers when the daemon is up.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export CONFIG_DIR="${CONFIG_DIR:-${REPO_ROOT}/homepage/config}"

exec python3 "${SCRIPT_DIR}/sync-homepage-services.py" "$@"
