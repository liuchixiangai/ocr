#!/usr/bin/env bash
# Submit a file (.zip of the app, or a signed .dmg) to Apple's notary service and
# wait for the verdict. Fails, printing Apple's log, unless it is "Accepted".
#
# Usage: scripts/ci/notarize_macos.sh <file>
# Needs APPLE_DEVELOPER_ID_USER (Apple ID), APPLE_DEVELOPER_ID_PASSWORD
# (app-specific password) and APPLE_DEVELOPER_ID_TEAM (team ID) in the environment.
set -euo pipefail

file="$1"
credentials=(
  --apple-id "$APPLE_DEVELOPER_ID_USER"
  --password "$APPLE_DEVELOPER_ID_PASSWORD"
  --team-id "$APPLE_DEVELOPER_ID_TEAM"
)

# the status in the JSON output decides, whatever notarytool's exit code
result="$(xcrun notarytool submit "$file" "${credentials[@]}" --wait --timeout 1h \
  --output-format json || true)"
echo "$result"
# notarytool prints a plain-text error, not JSON, when it can't sign in
field() {
  python3 -c '
import json, sys
try:
    print(json.loads(sys.stdin.read()).get(sys.argv[1], ""))
except ValueError:
    print("")
' "$1" <<<"$result"
}
id="$(field id)"
status="$(field status)"

if [ -z "$id" ]; then
  echo "::error::notarytool didn't submit $(basename "$file"): see its message above. A 401 means Apple rejected the credentials: check the APPLE_DEVELOPER_ID_USER (Apple ID), APPLE_DEVELOPER_ID_PASSWORD (an app-specific password from account.apple.com) and APPLE_DEVELOPER_ID_TEAM (team ID) secrets"
  exit 1
fi
if [ "$status" != "Accepted" ]; then
  echo "::error::Notarization of $(basename "$file") finished with status '$status'"
  if [ -n "$id" ]; then
    xcrun notarytool log "$id" "${credentials[@]}" || true
  fi
  exit 1
fi
