#!/usr/bin/env bash
# Staple the notarization ticket to a notarized .app or .dmg. Right after
# notarization, Apple's ticket service sometimes drops the connection ("The
# network connection was lost", stapler error 68), so this tries again for
# about 9 minutes before failing.
#
# Usage: scripts/ci/staple_macos.sh <file>
set -euo pipefail

file="$1"
attempts=10
for attempt in $(seq 1 "$attempts"); do
  if xcrun stapler staple "$file"; then
    exit 0
  fi
  if [ "$attempt" = "$attempts" ]; then
    echo "::error::Couldn't staple the notarization ticket to $(basename "$file") after $attempts attempts"
    exit 1
  fi
  echo "Stapling $(basename "$file") failed (attempt $attempt of $attempts); trying again in 60 s"
  sleep 60
done
