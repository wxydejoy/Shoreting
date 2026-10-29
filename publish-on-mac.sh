#!/usr/bin/env bash
set -euo pipefail
cd /Users/weiekko/dock
DIST="dist/Helm-红米K20"
NOTES="/Users/weiekko/dock/release-notes-v0.2.0.md"
GH=/opt/homebrew/bin/gh

if $GH release view v0.2.0 --repo wxydejoy/Helm >/dev/null 2>&1; then
  $GH release delete v0.2.0 --repo wxydejoy/Helm --yes
fi

$GH release create v0.2.0 \
  --repo wxydejoy/Helm \
  --title "v0.2.0" \
  --notes-file "$NOTES" \
  "$DIST/Helm-0.2.0.apk" \
  "$DIST/DockHub-0.2.0.exe"

$GH release view v0.2.0 --repo wxydejoy/Helm --web=false
