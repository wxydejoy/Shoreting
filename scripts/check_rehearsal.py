#!/usr/bin/env python3
"""预演线覆盖检查：收线或重建之后跑一遍，确认没有哪条分支的工作被落下。

背景：预演线重建过一次，丢过两块（F5 声纹整线、F7 安卓半场），都是事后翻代码才发现。
这个脚本把「哪条分支的补丁不在预演线上」变成一条机械命令。对每条本地分支给一个结论：

  merged     分支本身是 tip 的祖先——整条都进去了
  contained  补丁全在 tip 里（git cherry 只认补丁号，rebase 改写过的也认得）
  MISSING    有补丁在 tip 里对不上号——两种可能：内容被改写落线（adapted），
             或者又丢了一块。逐条人工核对，核对过的分支删掉或 --allow。

用法：
  uv run python scripts/check_rehearsal.py                    # 对 rehearsal/integration-hub
  uv run python scripts/check_rehearsal.py --tip 某分支        # 对别的线
  uv run python scripts/check_rehearsal.py --allow 旧分支       # 已核对过的豁免
  uv run python scripts/check_rehearsal.py --deep              # MISSING 分支加测内容存活率
MISSING 存在时退出码 1（放进 CI 或发版前的清单都行）。
"""

import argparse
import subprocess
import sys


def sh(*args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def sh_bytes(*args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
        errors="replace",
    )
    return out.stdout


def all_branches() -> list[str]:
    out = sh("for-each-ref", "refs/heads/", "--format=%(refname:short)")
    return [line for line in out.splitlines() if line]


def is_ancestor(branch: str, tip: str) -> bool:
    r = subprocess.run(
        ["git", "merge-base", "--is-ancestor", branch, tip],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def cherry(branch: str, tip: str) -> tuple[list[str], list[str]]:
    """返回 (不在 tip 里的提交, 已在 tip 里的提交)——git cherry 的 + / -。"""
    out = sh("cherry", tip, branch)
    plus, minus = [], []
    for line in out.splitlines():
        if not line.strip():
            continue
        flag, sha = line[:1], line[1:].strip()
        (plus if flag == "+" else minus).append(sha)
    return plus, minus


def subject(sha: str) -> str:
    return sh("show", "-s", "--format=%s", sha)


def tip_lines(tip: str) -> set[str]:
    """tip 上所有文件的所有行（去空白），用于内容级存活率判断。"""
    out = sh_bytes("ls-tree", "-r", "--name-only", tip)
    lines: set[str] = set()
    for path in out.splitlines():
        blob = subprocess.run(
            ["git", "show", f"{tip}:{path}"],
            capture_output=True, text=True, check=False, errors="replace",
        ).stdout
        lines.update(line.strip() for line in blob.splitlines())
    return lines


def added_lines(sha: str) -> list[str]:
    """一个提交里所有新增的、值得比对的行。"""
    keep: list[str] = []
    for line in sh_bytes("show", sha, "--no-color", "-U0", "--format=").splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        text = line[1:].strip()
        if len(text) < 14:  # 花括号、空行、短行不算数
            continue
        if text.startswith(("import ", "package ", "from ", "#include")):  # 到处都一样的行不算数
            continue
        keep.append(text)
    return keep


def survival(branch: str, shas: list[str], tip_set: set[str]) -> str:
    """分支缺的那几个提交，新增行有多少还活在 tip 上。低了才是真丢。"""
    total = found = 0
    for sha in shas:
        for text in added_lines(sha):
            total += 1
            if text in tip_set:
                found += 1
    if total == 0:
        return "n/a"
    return f"{found * 100 // total}%"


def main() -> int:
    ap = argparse.ArgumentParser(description="预演线覆盖检查")
    ap.add_argument("--tip", default="rehearsal/integration-hub", help="预演线分支（默认 rehearsal/integration-hub）")
    ap.add_argument("--allow", action="append", default=[], help="已人工核对过的分支名，可多次给")
    ap.add_argument("--skip-prefix", action="append", default=["shelve/", "archive/"],
                    help="不检查的分支名前缀（默认 shelve/ archive/）")
    ap.add_argument("--deep", action="store_true", help="给 MISSING 分支加测内容存活率（新增行还活在 tip 上的比例）")
    args = ap.parse_args()

    # shelve/ 是有意搁置的，archive/ 是归档自己人；tip 和 main 不用自己查自己
    skip = set(args.skip_prefix) | {args.tip, "main"}

    rows: list[tuple[str, str, list[str]]] = []  # (分支, 结论, 需要看的提交)
    for branch in sorted(all_branches()):
        if any(branch.startswith(p) for p in skip):
            continue
        if is_ancestor(branch, args.tip):
            rows.append((branch, "merged", []))
            continue
        plus, minus = cherry(branch, args.tip)
        if not plus:
            rows.append((branch, "contained", []))
        else:
            rows.append((branch, "MISSING", plus))

    width = max((len(b) for b, _, _ in rows), default=0)
    deep = tip_lines(args.tip) if args.deep and any(v == "MISSING" for _, v, _ in rows) else None
    missing = 0
    for branch, verdict, shas in rows:
        suffix = ""
        if verdict == "MISSING" and deep is not None:
            suffix = f"  (内容存活率 {survival(branch, shas, deep)})"
        if verdict == "MISSING":
            if branch in args.allow:
                verdict = "missing(allowed)"
            else:
                missing += 1
        print(f"{branch.ljust(width)}  {verdict}{suffix}")
        for sha in shas:
            print(f"{' ' * width}    · {sha[:9]} {subject(sha)}")

    print()
    if missing:
        print(f"{missing} 条分支的补丁不在 {args.tip} 上——先核对是「改写落线」还是「又丢了」；")
        print("确认是前者就把分支删掉（或打 archive/ 标签再删），是后者赶紧补。")
        return 1
    print(f"所有分支的补丁都在 {args.tip} 上，没有落下的。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
