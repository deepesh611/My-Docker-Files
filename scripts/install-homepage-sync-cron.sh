#!/usr/bin/env bash
# Idempotently install Homepage Docker sync on an hourly schedule.
# Prefers user crontab; on macOS TCC failures, installs a LaunchAgent instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SYNC_SCRIPT="${REPO_ROOT}/scripts/sync-homepage-services.sh"
LOG_FILE="${REPO_ROOT}/homepage/config/logs/sync-homepage.log"
CRON_LINE="0 * * * * ${SYNC_SCRIPT} >>${LOG_FILE} 2>&1"
INTERVAL_SECONDS=3600
LABEL="com.user.homepage-docker-sync"
LAUNCH_AGENT="${HOME}/Library/LaunchAgents/${LABEL}.plist"

mkdir -p "$(dirname "${LOG_FILE}")"
chmod +x "${SYNC_SCRIPT}" "${REPO_ROOT}/scripts/sync-homepage-services.py" "${REPO_ROOT}/scripts/install-homepage-sync-cron.sh"

install_crontab() {
  local tmp existing filtered
  tmp="$(mktemp)"
  existing="$(crontab -l 2>/dev/null || true)"
  if printf '%s\n' "${existing}" | grep -Fqx "${CRON_LINE}"; then
    echo "crontab entry already present"
    rm -f "${tmp}"
    return 0
  fi
  filtered="$(printf '%s\n' "${existing}" | grep -vF "${SYNC_SCRIPT}" || true)"
  {
    printf '%s\n' "${filtered}" | sed '/^$/d'
    printf '%s\n' "${CRON_LINE}"
  } >"${tmp}"
  if crontab "${tmp}"; then
    rm -f "${tmp}"
    echo "installed crontab entry:"
    echo "  ${CRON_LINE}"
    return 0
  fi
  rm -f "${tmp}"
  return 1
}

install_launchagent() {
  mkdir -p "${HOME}/Library/LaunchAgents"
  cat >"${LAUNCH_AGENT}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${SYNC_SCRIPT}</string>
  </array>
  <key>StartInterval</key>
  <integer>${INTERVAL_SECONDS}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_FILE}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_FILE}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "${LAUNCH_AGENT}"
  launchctl enable "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  echo "crontab unavailable; installed LaunchAgent:"
  echo "  ${LAUNCH_AGENT}"
  echo "  interval: ${INTERVAL_SECONDS}s (hourly)"
}

# Prefer updating an existing LaunchAgent in place on macOS.
if [[ -f "${LAUNCH_AGENT}" ]]; then
  install_launchagent
  exit 0
fi

if install_crontab; then
  exit 0
fi

echo "crontab failed (common on macOS without Full Disk Access); trying LaunchAgent..."
install_launchagent
