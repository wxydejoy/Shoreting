#!/usr/bin/env bash
# 一次发布，从头到尾：改版本号 → 跑测试 → 打包 → 发 GitHub Release。
#
# 用法：
#   scripts/release.sh 0.5.0 docs/releases/0.5.0.md
#   scripts/release.sh 0.5.0 --no-upload        # 只改版本、跑测试、打包
#   scripts/release.sh 0.5.0 --skip-tests       # 已经跑过了
#   scripts/release.sh 0.5.0 --skip-build       # 产物已经在 dist/
#
# 说明：
# - 版本号的唯一事实源是仓库根的 VERSION；五处影子由 scripts/bump_version.py 同步。
# - Hub 的 exe 只能在 Windows 上打（PyInstaller + mijiaAPI），Mac 上会自动跳过并提醒。
# - 上传用 gh CLI，仓库默认 wxydejoy/Shoreting，可用 REPO 覆盖。
# - 同名 release 已存在时先删再建：这是发布脚本，不是归档工具，重复跑要幂等。
set -euo pipefail

REPO="${REPO:-wxydejoy/Shoreting}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION=""
NOTES=""
SKIP_TESTS=0
SKIP_BUILD=0
NO_UPLOAD=0

say() { printf '\n=== %s\n' "$*"; }
die() { printf 'release: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    0.*|1.*|2.*) VERSION="$1"; shift ;;
    --skip-tests) SKIP_TESTS=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --no-upload) NO_UPLOAD=1; shift ;;
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) die "不认识的选项：$1" ;;
    *) NOTES="$1"; shift ;;
  esac
done

[[ -n "$VERSION" ]] || die "缺版本号，例如 scripts/release.sh 0.5.0 notes.md"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "版本号要三段式：0.5.0"
[[ -z "$NOTES" || -f "$NOTES" ]] || die "找不到发布说明：$NOTES"

python3 scripts/bump_version.py --check >/dev/null 2>&1 || true
say "版本：$(python3 scripts/bump_version.py --show)"
if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
  echo "注意：工作区不干净，发布前最好先提交。"
fi

say "1/4 同步版本号到 $VERSION"
python3 scripts/bump_version.py "$VERSION"

if [[ $SKIP_TESTS -eq 0 ]]; then
  say "2/4 跑测试"
  # hub 有 venv 就用它（macOS 上 python3 没装 PyYAML）；没有就退回 uv。
  if [[ -x hub/.venv/bin/python ]]; then
    (cd hub && .venv/bin/python -m unittest discover -s tests -q)
  elif command -v uv >/dev/null 2>&1; then
    (cd hub && uv run python -m unittest discover -s tests -q)
  else
    die "hub 没有 .venv，也没装 uv；先 cd hub && uv sync"
  fi
  if [[ -x mini/.venv/bin/python ]]; then
    (cd mini && .venv/bin/python -m unittest discover -s tests -q)
  fi
  if [[ -x console/.venv/bin/python ]]; then
    (cd console && .venv/bin/python -m unittest discover -s tests -q)
  fi
  if [[ -x android/gradlew ]]; then
    (cd android && ./gradlew --quiet :app:testDebugUnitTest)
  fi
else
  say "2/4 跳过测试（--skip-tests）"
fi

DIST="$ROOT/dist"
mkdir -p "$DIST"
APK="$DIST/Helm-$VERSION.apk"
EXE="$DIST/DockHub-$VERSION.exe"

if [[ $SKIP_BUILD -eq 0 ]]; then
  say "3/4 打包"
  if [[ -x android/gradlew ]]; then
    (cd android && ./gradlew --quiet :app:assembleDebug)
    built="$(ls -t android/app/build/outputs/apk/debug/*.apk | head -1)"
    cp "$built" "$APK"
    echo "apk → $APK"
  else
    echo "跳过安卓：没有 android/gradlew"
  fi
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      (cd hub && powershell -ExecutionPolicy Bypass -File ./build-exe.ps1)
      if [[ -f hub/dist/DockHub.exe ]]; then
        cp hub/dist/DockHub.exe "$EXE"
        echo "exe → $EXE"
      fi
      ;;
    *)
      echo "跳过 Hub exe：PyInstaller 只能在 Windows 上打（构建见 hub/README.md），打好了放到 $EXE 再重跑 --skip-build"
      ;;
  esac
else
  say "3/4 跳过打包（--skip-build）"
fi

ASSETS=()
if [[ -f "$APK" ]]; then ASSETS+=("$APK"); fi
if [[ -f "$EXE" ]]; then ASSETS+=("$EXE"); fi
[[ ${#ASSETS[@]} -gt 0 ]] || die "dist/ 里没有 v$VERSION 的产物，既没打包也没预先放好"

if [[ $NO_UPLOAD -eq 1 ]]; then
  say "4/4 跳过上传（--no-upload）"
  printf '产物：%s\n' "${ASSETS[@]}"
  exit 0
fi

command -v gh >/dev/null 2>&1 || die "没有 gh CLI，装一个或加 --no-upload"
say "4/4 发 GitHub Release v$VERSION（$REPO）"
if gh release view "v$VERSION" --repo "$REPO" >/dev/null 2>&1; then
  echo "v$VERSION 已存在，先删"
  gh release delete "v$VERSION" --repo "$REPO" --yes
fi

NOTES_ARG=(--generate-notes)
if [[ -n "$NOTES" ]]; then NOTES_ARG=(--notes-file "$NOTES"); fi

gh release create "v$VERSION" \
  --repo "$REPO" \
  --title "v$VERSION" \
  "${NOTES_ARG[@]}" \
  "${ASSETS[@]}"

say "完成"
gh release view "v$VERSION" --repo "$REPO" | head -5
