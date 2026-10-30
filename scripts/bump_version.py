#!/usr/bin/env python3
"""版本号只有一个地方写，其余四处都是它照出来的影子。

单一事实源是仓库根目录的 `VERSION`（一行 `0.5.0`）。`versionCode` 不进 VERSION：
它只在安卓包里要求单调递增，所以由这里按「上次发布是几」加一。

用法：

    python3 scripts/bump_version.py --check      # 五处不一致就非零退出（CI 用）
    python3 scripts/bump_version.py 0.5.0        # 同步改五处
    python3 scripts/bump_version.py 0.5.0 --dry-run
    python3 scripts/bump_version.py --show       # 打印当前版本与 versionCode

改之前先看 `git diff`：这个脚本只做字面替换，认不出「这句话被改写过」。
哪个文件的锚点找不到，它会直接报错退出，而不是悄悄跳过——版本号悄悄不同步，
比报错更难查。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"

# 每处锚点写成 (文件, 正则, 替换模板)。模板里 {version} / {code} 会被填。
SPOTS: list[tuple[str, str, str]] = [
    (
        "android/app/build.gradle.kts",
        r'versionCode = \d+',
        "versionCode = {code}",
    ),
    (
        "android/app/build.gradle.kts",
        r'versionName = "[^"]*"',
        'versionName = "{version}"',
    ),
    (
        "hub/pyproject.toml",
        r'^version = "[^"]*"$',
        'version = "{version}"',
    ),
    (
        "README.md",
        r'当前安卓 \*\*[0-9][^*]*\*\*',
        "当前安卓 **{version}**",
    ),
    (
        "android/README.md",
        r'当前 debug：\*\*[0-9][^*]*\*\*（`versionCode` \d+）',
        "当前 debug：**{version}**（`versionCode` {code}）",
    ),
    (
        "docs/companion.md",
        r'versionName `[^`]*`\*\*（`versionCode` \d+）',
        'versionName `{version}`**（`versionCode` {code}）',
    ),
]


def read_version() -> str:
    if not VERSION_FILE.is_file():
        raise SystemExit(f"找不到 {VERSION_FILE}，它应该是版本号的唯一事实源")
    text = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", text):
        raise SystemExit(f"VERSION 里应该是 0.5.0 这种三段式，现在是 {text!r}")
    return text


def read_code() -> int:
    path = ROOT / "android/app/build.gradle.kts"
    found = re.search(r"versionCode = (\d+)", path.read_text(encoding="utf-8"))
    if not found:
        raise SystemExit("build.gradle.kts 里找不到 versionCode")
    return int(found.group(1))


def scan(version: str, code: int) -> list[tuple[str, str, str]]:
    """返回 (文件, 现状, 应该是) 的不一致清单。"""
    drift: list[tuple[str, str, str]] = []
    for rel, pattern, template in SPOTS:
        path = ROOT / rel
        if not path.is_file():
            drift.append((rel, "文件不存在", "存在"))
            continue
        text = path.read_text(encoding="utf-8")
        want = template.format(version=version, code=code)
        matches = re.findall(pattern, text, flags=re.MULTILINE)
        if not matches:
            drift.append((rel, "找不到锚点", want))
            continue
        for have in matches:
            if have != want:
                drift.append((rel, have, want))
    return drift


def apply(version: str, code: int, *, current: str, dry_run: bool) -> list[str]:
    changed: list[str] = []
    for rel, pattern, template in SPOTS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        want = template.format(version=version, code=code)
        new_text, count = re.subn(pattern, want.replace("\\", "\\\\"), text, flags=re.MULTILINE)
        if count == 0:
            raise SystemExit(f"{rel} 里找不到锚点：{pattern}")
        if new_text != text:
            changed.append(f"{rel}: {count} 处 → {want}")
            if not dry_run:
                path.write_text(new_text, encoding="utf-8")
    if version != current:
        changed.append(f"VERSION: {version}")
        if not dry_run:
            VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    return changed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="同步版本号（VERSION 是唯一事实源）")
    parser.add_argument("version", nargs="?", help="新的版本号，如 0.5.0")
    parser.add_argument("--check", action="store_true", help="只检查五处是否一致")
    parser.add_argument("--show", action="store_true", help="打印当前版本与 versionCode")
    parser.add_argument("--dry-run", action="store_true", help="只打印要改什么")
    args = parser.parse_args(argv)

    current = read_version()
    code = read_code()

    if args.show:
        print(f"version={current} versionCode={code}")
        return 0

    if args.check:
        drift = scan(current, code)
        for rel, have, want in drift:
            print(f"漂移 {rel}: {have} → {want}")
        if drift:
            print(f"{len(drift)} 处和 VERSION={current} 对不上")
            return 1
        print(f"版本一致：{current}（versionCode {code}）")
        return 0

    if not args.version:
        parser.error("要么给一个新版本号，要么用 --check / --show")

    version = args.version
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"版本号要三段式（0.5.0），收到 {version!r}", file=sys.stderr)
        return 2

    next_code = code if version == current else code + 1
    if version == current:
        print(f"VERSION 已经是 {version}，只补其它几处")
    changed = apply(version, next_code, current=current, dry_run=args.dry_run)
    for line in changed:
        print(("会改 " if args.dry_run else "改了 ") + line)
    if not changed:
        print("五处已经一致，什么都没动")
    else:
        print(f"versionName={version} versionCode={next_code}")
        if not args.dry_run:
            print("下一步：跑测试 → 打包 → scripts/release.sh 上传")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
