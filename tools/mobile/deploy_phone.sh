#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'USAGE'
Usage:
  tools/mobile/deploy_phone.sh <target> [options]

Targets:
  android    Deploy Android app from Linux/macOS using adb + Gradle
  ios        Deploy iOS app (Linux => SSH macOS, macOS => local Xcode)
  ios-ssh    Force Linux-style iOS deploy via SSH to macOS
  ios-local  Force local iOS deploy on macOS

Examples:
  tools/mobile/deploy_phone.sh android --server-url http://192.168.1.20:8765
  tools/mobile/deploy_phone.sh ios --mac-host dev@macmini.local --server-url http://192.168.1.20:8765 --device-udid <udid> --apple-team-id <team-id>
  tools/mobile/deploy_phone.sh ios-local --server-url http://192.168.1.20:8765 --device-udid <udid> --apple-team-id <team-id>

Tip:
  Run "<target> --help" by passing --help after the target.
  Example: tools/mobile/deploy_phone.sh ios --help
USAGE
}

fail() {
  echo "error: $*" >&2
  exit 1
}

if (($# == 0)); then
  usage
  exit 1
fi

case "${1}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

TARGET="$1"
shift

case "${TARGET}" in
  android)
    exec "${SCRIPT_DIR}/deploy_android.sh" "$@"
    ;;
  ios)
    if [[ "$(uname -s)" == "Darwin" ]]; then
      exec "${SCRIPT_DIR}/deploy_ios_on_macos.sh" "$@"
    fi
    exec "${SCRIPT_DIR}/deploy_ios_via_ssh.sh" "$@"
    ;;
  ios-ssh)
    exec "${SCRIPT_DIR}/deploy_ios_via_ssh.sh" "$@"
    ;;
  ios-local)
    exec "${SCRIPT_DIR}/deploy_ios_on_macos.sh" "$@"
    ;;
  *)
    fail "unknown target: ${TARGET}"
    ;;
esac
