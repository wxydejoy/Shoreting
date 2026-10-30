#!/usr/bin/env python3
"""对一遍账：server.py 真正在服务的路由，docs/openapi.yaml 有没有写。

为什么要有这个东西：协议先是写在 docs/lan-protocol.md，后来才有了 openapi.yaml，
两边都靠人记，结果就是文档里查得到的接口和代码里能调的接口慢慢不是一回事。
手写文档会漂，机械比对不会——所以把「漂了没有」变成一条命令。

用法：
    python3 scripts/check_protocol.py            # 在仓库任意位置跑都行
    python3 scripts/check_protocol.py --list     # 顺便打印解析出来的路由
    python3 scripts/check_protocol.py --quiet    # 只在出问题时说话

退出码：0 一致；1 有代码路由没进文档（或文档写死的路由代码里没有）；2 解析失败。

只看 /v1 开头和 /health。`/chats`、`/read`、`/setup` 是给人点的 HTML 页面，
不属于协议面，改了也没人需要重新对接，所以不计入。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 代码里认三种写法：
#   if path == "/v1/weather":
#   if path == "/v1/books" or path.startswith("/v1/books/"):
#   if path.startswith("/v1/companion/audio/"):
EXACT = re.compile(r'path\s*==\s*"([^"]+)"')
PREFIX = re.compile(r'path\.startswith\(\s*"([^"]+)"')
METHOD_DEF = re.compile(r"def\s+do_(GET|POST|PUT|DELETE|PATCH)\s*\(")
# 有的分支不写字面量，先把前缀存进变量再判断：books_prefix = "/v1/companion/books/"
ASSIGN = re.compile(r'^\s*(\w+)\s*=\s*"([^"]*)"\s*$', re.M)
VAR_PREFIX = re.compile(r"path\.startswith\(\s*(\w+)\s*\)")

# 「头 + 尾巴」拼出来的路径，上面几种写法都看不出来，只能显式登记。
# 左边 (方法, 模板)，右边这几段字面量必须同时出现在同一个 do_XXX 里。
COMPOSED = [
    ("POST", "/v1/devices/{id}/command", ('"/v1/devices/"', '"/command"')),
    ("POST", "/v1/scenes/{id}", ('"/v1/scenes/"',)),
]

DOCUMENTED_KINDS = ("/v1", "/health")


def repo_root(script: Path) -> Path:
    here = script.resolve()
    for parent in here.parents:
        if (parent / "hub" / "dock_hub" / "server.py").exists():
            return parent
    raise SystemExit("找不到仓库根目录（往上没有 hub/dock_hub/server.py）")


def split_methods(source: str) -> list[tuple[str, str]]:
    """把 server.py 切成 (HTTP 方法, 该方法的源码) 片段。"""
    marks = [(m.start(), m.group(1)) for m in METHOD_DEF.finditer(source)]
    if not marks:
        return []
    chunks: list[tuple[str, str]] = []
    for index, (start, method) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(source)
        chunks.append((method, source[start:end]))
    return chunks


def normalise_prefix(prefix: str) -> str:
    return prefix.rstrip("/")


def code_routes(source: str) -> list[tuple[str, str, bool]]:
    """返回 [(方法, 路径, 是否前缀匹配)]。"""
    routes: dict[tuple[str, str], bool] = {}

    def add(method: str, path: str, is_prefix: bool) -> None:
        key = (method, normalise_prefix(path))
        # 同一路径既有精确判断又有前缀判断时按前缀算：宽的那条才是真正在服务的范围。
        routes[key] = routes.get(key, False) or is_prefix

    chunks = split_methods(source)
    for method, chunk in chunks:
        for path in EXACT.findall(chunk):
            add(method, path, False)
        for path in PREFIX.findall(chunk):
            add(method, path, True)
        literals = dict(ASSIGN.findall(chunk))
        # 同时判断 startswith 和 endswith 的（头一段 + 尾一段拼出来），
        # 前缀只是整条路径的一半，交给 COMPOSED 认，别在这里当成一个路由家族。
        if "path.endswith(" not in chunk:
            for name in VAR_PREFIX.findall(chunk):
                if name in literals and literals[name].startswith("/"):
                    add(method, literals[name], True)

    by_method = {method: chunk for method, chunk in chunks}
    for method, template, needles in COMPOSED:
        chunk = by_method.get(method, "")
        if all(needle in chunk for needle in needles):
            add(method, template, False)

    return [(method, path, is_prefix) for (method, path), is_prefix in routes.items()]


def documented_routes(openapi_text: str) -> list[tuple[str, str]]:
    """从 openapi.yaml 里读 paths 下的 方法 + 路径。

    故意不用 pyyaml：这个脚本要能在任何一台机器上裸 python3 跑起来，
    多一个依赖就多一个「本地跑不了、CI 才跑」的借口。
    够用就好的解析方式：paths: 之后、下一个顶格键之前，两格缩进的是路径，
    四格缩进的 get/post 是方法。
    """
    routes: list[tuple[str, str]] = []
    in_paths = False
    current: str | None = None
    for raw in openapi_text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if indented == 0:
            in_paths = stripped == "paths:"
            current = None
            continue
        if not in_paths:
            continue
        if indented == 2 and stripped.startswith("/"):
            current = stripped.rstrip(":").split(":")[0].strip()
            continue
        if indented == 4 and current:
            word = stripped.split(":")[0].strip().lower()
            if word in ("get", "post", "put", "delete", "patch"):
                routes.append((word.upper(), current))
    return routes


def covered(code_path: str, is_prefix: bool, documented: set[str]) -> bool:
    if code_path in documented:
        return True
    if not is_prefix:
        return False
    head = code_path + "/"
    for path in documented:
        if not path.startswith(head):
            continue
        rest = [part for part in path[len(head) :].split("/") if part]
        if rest and all(part.startswith("{") and part.endswith("}") for part in rest):
            return True
    return False


def stale_hint(doc_path: str, code_paths: list[str]) -> bool:
    """文档里的路径，代码里一个前缀都对不上——多半是删接口时忘了删文档。"""
    stem = doc_path.split("{")[0].rstrip("/")
    return not any(path == stem or path.startswith(stem + "/") or stem.startswith(path + "/") for path in code_paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="代码路由 ↔ openapi.yaml 对账")
    parser.add_argument("--root", type=Path, default=None, help="仓库根目录，默认自动往上找")
    parser.add_argument("--list", action="store_true", help="打印解析结果")
    parser.add_argument("--quiet", action="store_true", help="只在出问题时输出")
    args = parser.parse_args(argv)

    root = args.root.resolve() if args.root else repo_root(Path(__file__))
    server = root / "hub" / "dock_hub" / "server.py"
    spec = root / "docs" / "openapi.yaml"
    for needed in (server, spec):
        if not needed.exists():
            print(f"缺文件：{needed}", file=sys.stderr)
            return 2

    routes = code_routes(server.read_text(encoding="utf-8"))
    docs = documented_routes(spec.read_text(encoding="utf-8"))
    if not routes:
        print("server.py 里一条路由都没解析出来，正则和代码对不上了", file=sys.stderr)
        return 2
    if not docs:
        print("openapi.yaml 里一条 path 都没解析出来，格式可能变了", file=sys.stderr)
        return 2

    documented = {path for _, path in docs}
    doc_pairs = set(docs)
    code_paths = [path for _, path, _ in routes]

    missing = [
        (method, path, is_prefix)
        for method, path, is_prefix in routes
        if path.startswith(DOCUMENTED_KINDS)
        and not covered(path, is_prefix, documented)
    ]
    # 方法级漏写也算漏：路径在文档里，但这个动词没写。
    for method, path, is_prefix in list(routes):
        if (method, path) in doc_pairs or not covered(path, is_prefix, documented):
            continue
        if path in documented and not any(m == method and p == path for m, p in docs):
            entry = (method, path, is_prefix)
            if entry not in missing:
                missing.append(entry)

    stale = [
        (method, path)
        for method, path in docs
        if stale_hint(path, code_paths)
    ]

    if args.list:
        print(f"代码路由 {len(routes)} 条，文档 {len(docs)} 条")
        for method, path, is_prefix in routes:
            mark = "prefix" if is_prefix else "exact"
            print(f"  code {method:5} {path}  ({mark})")
        for method, path in docs:
            print(f"  doc  {method:5} {path}")

    if missing:
        print(f"有 {len(missing)} 条路由在代码里活着，但 openapi.yaml 没写：")
        for method, path, is_prefix in missing:
            hint = "（前缀路由，文档里写完整模板，如 /x/{id}/y）" if is_prefix else ""
            print(f"  {method:5} {path}{hint}")
        print("补 docs/openapi.yaml 的 paths，再回来跑一遍。")

    if stale:
        print(f"有 {len(stale)} 条文档路由在代码里找不到对应分支（多半是删接口忘了删文档）：")
        for method, path in stale:
            print(f"  {method:5} {path}")

    if not missing and not stale:
        if not args.quiet:
            print(f"一致：代码 {len(routes)} 条路由 / 文档 {len(docs)} 条，没有漂移。")
        return 0
    return 1 if (missing or stale) else 0


if __name__ == "__main__":
    sys.exit(main())
