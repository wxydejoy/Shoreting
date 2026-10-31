#!/usr/bin/env bash
# 发布一个版本到 GitHub Releases。
#
#   scripts/publish-release.sh 0.4.6
#
# 约定（以前每个版本抄一份 publish-on-mac.sh，版本号和绝对路径都硬编码在里面）：
#   - 说明文件：release-<版本>/notes.md
#   - 安装包：  dist/Helm-红米K20/Helm-<版本>.apk、DockHub-<版本>.exe
#   - 存在哪个发哪个，两个都没有就报错
set -euo pipefail

VERSION="${1:-}"
REPO="${REPO:-wxydejoy/Helm}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$ROOT/dist/Helm-红米K20"
GH="${GH:-$(command -v gh || true)}"
[[ -n "$GH" ]] || GH="/opt/homebrew/bin/gh"

if [[ -z "$VERSION" ]]; then
  echo "用法：$(basename "$0") <版本号>    例：$(basename "$0") 0.4.6" >&2
  echo "说明放 release-<版本号>/notes.md，安装包放 dist/Helm-红米K20/" >&2
  exit 2
fi
VERSION="${VERSION#v}"
TAG="v$VERSION"

NOTES="$ROOT/release-$VERSION/notes.md"
if [[ ! -f "$NOTES" ]]; then
  echo "缺少发布说明：$NOTES" >&2
  exit 2
fi
if [[ ! -x "$GH" ]]; then
  echo "找不到 gh：先 brew install gh 或设置 GH=/path/to/gh" >&2
  exit 2
fi

assets=()
for file in "$DIST/Helm-$VERSION.apk" "$DIST/DockHub-$VERSION.exe"; do
  [[ -f "$file" ]] && assets+=("$file")
done
if [[ ${#assets[@]} -eq 0 ]]; then
  echo "$DIST 里没有 $VERSION 的 apk 或 exe，先打包再发布" >&2
  exit 2
fi

if "$GH" release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  echo "已有 $TAG，先删掉再建" >&2
  "$GH" release delete "$TAG" --repo "$REPO" --yes
fi

"$GH" release create "$TAG" \
  --repo "$REPO" \
  --title "$TAG" \
  --notes-file "$NOTES" \
  "${assets[@]}"

"$GH" release view "$TAG" --repo "$REPO" --web=false
