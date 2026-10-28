# 睡前朗读 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 `/read` 上从 `books/` 里的 txt 拆章朗读，跨次记住字偏移，并在播放满设定时长后停下。

**Architecture:** 新的 `dock_hub/books.py` 管书架、拆章、书签、播放线程和定时。它通过一个很小的喇叭接口调用 `Companion` 的逐句送出和停止。短稿仍走原来的 read job。页面在同一份 `read.html` 里增加故事视图。

**Tech Stack:** Python 3.10、标准库 `unittest`、`zh-chapter-splitter`、现有 `ThreadingHTTPServer` 测试。

**Spec:** `docs/superpowers/specs/2026-10-27-bedtime-reading-design.md`

## Global Constraints

- 书只放在 Hub 配置目录的 `books/`，与 `reads/` 并列。只认这一层、后缀正好是 `.txt` 的文件。
- 书 id 是文件名。拒绝空名、`/`、`\`、`..`、不是 `.txt` 的名字。解析后的路径必须仍在 `books/` 里。
- 上传正文上限 32MB（`32 * 1024 * 1024`）。不要把 `server.py` 里现有的 `MAX_BODY`（64KB）放大。
- 书签偏移是各章 `content` 顺序拼成的字符串下标，章与章之间不加字符。`end` 不含。
- 定时分钟只允许 `0`、`15`、`30`、`45`、`60`。默认 `30`。`0` 表示关。
- 打开页面不出声。Hub 重启后不接着念。倒计时只在内存里。
- 短稿的八千字上限、任务队列、生成 WAV 的行为不变。故事播放不得创建短稿任务。
- 错误体保持 `{"error": {"code", "message"}}`，`message` 用中文。
- 这些新路由与 `/read` 相同：本机免 Token，局域网要 Token。安卓不调用。
- 测试用 `cd hub && uv run python -m unittest <模块> -v`。等待用注入的时钟，禁止在测试里真睡预估秒数。
- 本目录目前不是 git 仓库，用户也没要求提交。每个任务末尾的 Commit 步骤先跳过，不要为此执行 `git init`。

---

## 文件

- 新建 `hub/dock_hub/books.py`：书架、进度、拆章、切片、播放线程、定时。
- 新建 `hub/tests/test_books.py`：上述行为的单元测试和 HTTP 测试。
- 修改 `hub/dock_hub/companion.py`：增加故事用的送句 / 停喇叭，并在 `stop` 与 `read` 里通知书架。
- 修改 `hub/dock_hub/service.py`：创建 `Books`，目录设在配置目录的 `books/`。
- 修改 `hub/dock_hub/server.py`：书架与故事路由；上传走单独的 32MB 读取。
- 修改 `hub/dock_hub/assets/read.html`：故事视图、目录、定时；短稿留在同一页。
- 修改 `hub/pyproject.toml`：加入 `zh-chapter-splitter`。
- 修改 `docs/lan-protocol.md`、`hub/README.md`。

后面任务共用的类型（实现时放在 `books.py`，测试可以构造假对象）：

```python
class StoryClock:
    """monotonic() -> float；wall() -> int；wait(seconds, interrupt, deadline) -> 'elapsed' | 'stopped' | 'deadline'。"""

class StorySpeaker:
    """cue(text: str) -> bool；stop() -> None；cancel_short_reads() -> None。"""
```

故事状态字典的键固定为：`playing`、`book_id`、`offset`、`chapter`、`chapter_title`、`sentence`、`sentence_start`、`chars`、`chapters`、`timer_min`、`timer_remaining_sec`、`stopped_by`、`error`。

---

### Task 1: 书架文件与进度

**Files:**
- Create: `hub/dock_hub/books.py`
- Test: `hub/tests/test_books.py`

**Interfaces:**
- Consumes: `dock_hub.errors.HubError`
- Produces:
  - `TIMER_CHOICES = frozenset({0, 15, 30, 45, 60})`
  - `MAX_BOOK_BYTES = 32 * 1024 * 1024`
  - `safe_book_id(name: str) -> str` — 非法时 `HubError("bad_request", ...)`
  - `load_progress(path: Path) -> dict` — 形如 `{"current_id": str | None, "timer_min": int, "books": {id: {"offset": int, "updated_at": int}}}`
  - `Books(root: Path)`
  - `Books.list_books() -> dict`
  - `Books.save_upload(filename: str, data: bytes) -> dict` — `{"id", "bytes"}`，并写成 `current_id`

- [ ] **Step 1: Write the failing test**

```python
import json
import tempfile
import unittest
from pathlib import Path

from dock_hub.books import MAX_BOOK_BYTES, Books, load_progress, safe_book_id
from dock_hub.errors import HubError


class ShelfTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.books = Books(self.root)

    def test_rejects_unsafe_names(self) -> None:
        for name in ("", "a.md", "../x.txt", "a/b.txt", "a\\b.txt", "nodot"):
            with self.assertRaises(HubError) as caught:
                safe_book_id(name)
            self.assertEqual(caught.exception.status, 400)

    def test_lists_only_top_level_txt(self) -> None:
        (self.root / "雾海.txt").write_bytes("甲".encode())
        (self.root / "notes.md").write_text("no", encoding="utf-8")
        (self.root / "progress.json").write_text("{}", encoding="utf-8")
        nested = self.root / "sub"
        nested.mkdir()
        (nested / "隐藏.txt").write_text("藏", encoding="utf-8")
        body = self.books.list_books()
        self.assertEqual([item["id"] for item in body["items"]], ["雾海.txt"])
        self.assertEqual(body["items"][0]["title"], "雾海")
        self.assertEqual(body["timer_min"], 30)
        self.assertIsNone(body["current_id"])

    def test_upload_sets_current_without_byte_clamping(self) -> None:
        saved = self.books.save_upload("雾海.txt", "一二三四五".encode())
        self.assertEqual(saved, {"id": "雾海.txt", "bytes": len("一二三四五".encode())})
        listed = self.books.list_books()
        self.assertEqual(listed["current_id"], "雾海.txt")
        progress = self.root / "progress.json"
        progress.write_text(
            json.dumps(
                {
                    "current_id": "雾海.txt",
                    "timer_min": 15,
                    "books": {"雾海.txt": {"offset": 2, "updated_at": 1}, "没了.txt": {"offset": 9, "updated_at": 1}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.books.save_upload("雾海.txt", "一二".encode())
        kept = json.loads(progress.read_text(encoding="utf-8"))
        self.assertEqual(kept["books"]["雾海.txt"]["offset"], 2)
        self.assertNotIn("没了.txt", kept["books"])
        self.assertEqual(kept["timer_min"], 15)

    def test_rejects_oversize_upload(self) -> None:
        with self.assertRaises(HubError) as caught:
            self.books.save_upload("大.txt", b"x" * (MAX_BOOK_BYTES + 1))
        self.assertEqual(caught.exception.status, 400)
        self.assertFalse((self.root / "大.txt").exists())

    def test_broken_progress_is_default(self) -> None:
        (self.root / "progress.json").write_text("{", encoding="utf-8")
        body = load_progress(self.root / "progress.json")
        self.assertEqual(body, {"current_id": None, "timer_min": 30, "books": {}})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && uv run python -m unittest tests.test_books.ShelfTest -v`

Expected: FAIL，`dock_hub.books` 不存在。

- [ ] **Step 3: Write minimal implementation**

`safe_book_id` 用 `Path(name).name == name` 且 `name.endswith(".txt")` 且 `name` 不是 `progress.json`。`load_progress` 在文件缺失、JSON 损坏、字段类型不对时返回默认值。`timer_min` 不在 `TIMER_CHOICES` 里就改回 30。`save_upload` 先校验大小和文件名，写入 `root / book_id`，把 `current_id` 设为它。这里不要按字节长度改书签；字数要等 Task 2 拆章后才知道。列表按文件名排序。`title` 是去掉末尾 `.txt` 的文件名。`bytes` 与 `mtime`（`int(stat().st_mtime)`）来自文件。`save_upload` 写进度时丢掉 id 已经不在目录里的项，并保留原来的 `timer_min`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && uv run python -m unittest tests.test_books.ShelfTest -v`

Expected: PASS

- [ ] **Step 5: Commit**

跳过。见 Global Constraints。

---

### Task 2: 拆章与打开一本书

**Files:**
- Modify: `hub/pyproject.toml`
- Modify: `hub/dock_hub/books.py`
- Modify: `hub/tests/test_books.py`

**Interfaces:**
- Consumes: Task 1 的 `Books`、`safe_book_id`
- Produces:
  - `Document`：`encoding: str`、`text: str`、`chapters: list[Chapter]`、`problem_label: str`
  - `Chapter`：`index: int`、`title: str`、`volume: str`、`start: int`、`end: int`、`text: str`
  - `split_document(raw: bytes, fallback_title: str, split_fn=None) -> Document`
  - `Books.open_book(book_id: str) -> dict`
  - `Books.chapter(book_id: str, index: int) -> dict`
  - `Books._document(book_id: str) -> Document` — 按 `(path, size, mtime_ns)` 缓存

`open_book` 返回 `id`、`title`、`encoding`、`chars`、`bookmark`、`chapters`（`index/title/volume/start/end`）、`problem_label`。`chapter` 再加 `text`。

- [ ] **Step 1: Write the failing test**

```python
class SplitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_plain_text_is_one_chapter(self) -> None:
        raw = "只有正文。没有标题。".encode()
        doc = split_document(raw, "随笔")
        self.assertEqual(len(doc.chapters), 1)
        self.assertEqual(doc.chapters[0].title, "随笔")
        self.assertIn("只有正文", doc.text)
        self.assertEqual(doc.chapters[0].start, 0)
        self.assertEqual(doc.chapters[0].end, len(doc.text))

    def test_marked_chapters_join_without_extra_chars(self) -> None:
        raw = "第一章 雾\n\n海雾来了。\n\n第二章 岸\n\n灯还亮着。\n".encode()
        doc = split_document(raw, "雾海")
        self.assertGreaterEqual(len(doc.chapters), 2)
        self.assertEqual(doc.text, "".join(ch.text for ch in doc.chapters))
        self.assertEqual(doc.chapters[0].start, 0)
        self.assertEqual(doc.chapters[1].start, doc.chapters[0].end)
        self.assertIn("海雾来了", doc.text)
        self.assertIn("灯还亮着", doc.text)

    def test_splitter_exception_becomes_one_chapter(self) -> None:
        def boom(_text: str):
            raise RuntimeError("split failed")

        doc = split_document("整篇都在。".encode(), "雾海", split_fn=boom)
        self.assertEqual(len(doc.chapters), 1)
        self.assertEqual(doc.chapters[0].title, "雾海")
        self.assertIn("整篇都在", doc.text)

    def test_open_returns_toc_without_body(self) -> None:
        (self.root / "雾海.txt").write_bytes("第一章 雾\n\n海雾来了。\n\n第二章 岸\n\n灯还亮着。\n".encode())
        books = Books(self.root)
        opened = books.open_book("雾海.txt")
        self.assertNotIn("text", opened)
        self.assertGreaterEqual(len(opened["chapters"]), 2)
        self.assertEqual(opened["bookmark"], 0)
        chapter = books.chapter("雾海.txt", 0)
        self.assertIn("海雾", chapter["text"])
        with self.assertRaises(HubError) as caught:
            books.chapter("雾海.txt", 99)
        self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(HubError) as missing:
            books.open_book("没有.txt")
        self.assertEqual(missing.exception.status, 404)

    def test_overlong_bookmark_clamps_to_document_end(self) -> None:
        (self.root / "雾海.txt").write_bytes("海雾来了。".encode())
        (self.root / "progress.json").write_text(
            json.dumps({"current_id": "雾海.txt", "timer_min": 30, "books": {"雾海.txt": {"offset": 9999, "updated_at": 1}}}),
            encoding="utf-8",
        )
        books = Books(self.root)
        opened = books.open_book("雾海.txt")
        self.assertEqual(opened["bookmark"], opened["chars"])
        disk = json.loads((self.root / "progress.json").read_text(encoding="utf-8"))
        self.assertEqual(disk["books"]["雾海.txt"]["offset"], opened["chars"])

    def test_undecodable_book_raises(self) -> None:
        (self.root / "坏.txt").write_bytes(b"\xff\xfe\x00\x00\xff")
        books = Books(self.root)
        with self.assertRaises(HubError) as caught:
            books.open_book("坏.txt")
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("读不出", caught.exception.message)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && uv run python -m unittest tests.test_books.SplitTest -v`

Expected: FAIL，`split_document` 未定义。

- [ ] **Step 3: Write minimal implementation**

在 `hub/pyproject.toml` 的 `dependencies` 加入 `zh-chapter-splitter`，然后 `cd hub && uv lock && uv sync`。把锁到的版本写回 `pyproject.toml` 的下限（`zh-chapter-splitter>=解析到的版本`）。

`split_document`：

1. `decode_bytes(raw)` 得到文本和 `encoding`。解码失败抛 `HubError("bad_request", "这本书读不出")`。
2. `clean_text`。
3. 调用 `split_fn` 或 `split_chapters_with_report`。异常、空列表、或没有 `content` 时，合成一章：`title` 用 `fallback_title`，`content` 用清洗后的全文。
4. 按返回顺序拼接每章的 `content`（缺省当空字符串）。`start` 为拼接前的长度，`end` 为拼接后的长度。`volume` 取 `volume_title`，没有则为 `""`。`problem_label` 取报告里的字符串，没有则为 `""`。作者说明章不要丢掉。

`Books._document` 缓存键是 `(resolve 后的路径, st_size, st_mtime_ns)`。`open_book` / `chapter` 用 `safe_book_id`，文件不存在就是 404「没有这本书」。章序号越界是 404「没有这一章」。`bookmark` 来自进度。大于 `chars` 的偏移写成 `chars` 并落盘（与读完时的末尾标记相同；空书为 0）。等于 `chars` 的偏移保持不变，留给 Task 4 的「再播放从 0 开始」。解码失败抛 `HubError("bad_request", "这本书读不出")`。若某个字节序列其实能被拆章器解出来，把测试里的 `坏.txt` 换成拆章器会拒绝的输入，断言仍是 400 且消息含「读不出」。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && uv run python -m unittest tests.test_books.SplitTest tests.test_books.ShelfTest -v`

Expected: PASS。若真实拆章器把「第一章」标题算进 `content`，断言仍然成立，不要改成写死标题文案。

- [ ] **Step 5: Commit**

跳过。

---

### Task 3: 下一段连续切片

**Files:**
- Modify: `hub/dock_hub/books.py`
- Modify: `hub/tests/test_books.py`

**Interfaces:**
- Consumes: `dock_hub.companion.chunk_read_text`、`pack_read_chunks`、`_PUNCT_ONLY`
- Produces: `next_slice(text: str, offset: int) -> tuple[int, str] | None`

返回值是 `(sentence_start, sentence)`。`sentence` 必须等于 `text[sentence_start:sentence_start + len(sentence)]`。`offset >= len(text)` 时返回 `None`。

- [ ] **Step 1: Write the failing test**

```python
class SliceTest(unittest.TestCase):
    def test_reads_a_contiguous_sentence(self) -> None:
        text = "海雾来了。岸边有灯。"
        start, piece = next_slice(text, 0)
        self.assertEqual(text[start : start + len(piece)], piece)
        self.assertTrue(piece.startswith("海雾来了"))
        nxt = next_slice(text, start + len(piece))
        self.assertIsNotNone(nxt)
        self.assertIn("岸边有灯", nxt[1])

    def test_skips_leading_punctuation(self) -> None:
        text = "。。海雾来了。"
        start, piece = next_slice(text, 0)
        self.assertFalse(piece.startswith("。"))
        self.assertEqual(text[start : start + len(piece)], piece)

    def test_done_at_end(self) -> None:
        self.assertIsNone(next_slice("海雾来了。", 5))
        self.assertIsNone(next_slice("。。", 0))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && uv run python -m unittest tests.test_books.SliceTest -v`

Expected: FAIL，`next_slice` 未定义。

- [ ] **Step 3: Write minimal implementation**

从 `offset` 起跳过 `_PUNCT_ONLY` 能匹配的单字，得到 `start`。对 `text[start:]` 做 `pack_read_chunks(chunk_read_text(...))`。取第一段；若它是窗口的前缀，就返回 `(start, piece)`。否则改用第一段非空、且是窗口前缀、且不是纯标点的原始 `chunk_read_text` 片段。再否则返回 `(start, text[start])`，保证不会停在原地。窗口空了就返回 `None`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && uv run python -m unittest tests.test_books.SliceTest -v`

Expected: PASS

- [ ] **Step 5: Commit**

跳过。

---

### Task 4: 播放、暂停、跨章、读完

**Files:**
- Modify: `hub/dock_hub/books.py`
- Modify: `hub/tests/test_books.py`

**Interfaces:**
- Consumes: `next_slice`、`estimate_read_seconds`、`StoryClock`、`StorySpeaker`
- Produces:
  - `Books(root, speaker=None, clock=None, split_fn=None)`
  - `Books.play(book_id: str, offset: int | None = None) -> dict`
  - `Books.pause() -> dict`
  - `Books.bookmark(book_id: str, offset: int) -> dict`
  - `Books.story() -> dict`

`play` 在已经播放时是跳转：改读指针，倒计时不动（本任务倒计时可以还是空的）。没在读时 `bookmark` 只改书签和 `current_id`。正在读时 `bookmark` 抛 `HubError("bad_request", "正在读，用播放接口跳转")`。

- [ ] **Step 1: Write the failing test**

```python
import threading

from dock_hub.companion import estimate_read_seconds


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.waiting = threading.Event()
        self._release = threading.Event()

    def monotonic(self) -> float:
        return self.now

    def wall(self) -> int:
        return 1_700_000_000

    def wait(self, seconds: float, interrupt: threading.Event, deadline: float | None) -> str:
        self.waiting.set()
        while not interrupt.is_set():
            if self._release.wait(timeout=0.05):
                self.now += seconds
                self._release.clear()
                self.waiting.clear()
                if deadline is not None and self.now >= deadline:
                    return "deadline"
                return "elapsed"
        self.waiting.clear()
        return "stopped"

    def release(self) -> None:
        self._release.set()


class FakeSpeaker:
    def __init__(self) -> None:
        self.cued: list[str] = []
        self.stops = 0
        self.cancelled = 0
        self.fail = False

    def cue(self, text: str) -> bool:
        if self.fail:
            return False
        self.cued.append(text)
        return True

    def stop(self) -> None:
        self.stops += 1

    def cancel_short_reads(self) -> None:
        self.cancelled += 1


class PlayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "雾海.txt").write_bytes(
            "第一章 雾\n\n海雾来了。岸边有灯。\n\n第二章 岸\n\n灯还亮着。\n".encode()
        )
        self.clock = FakeClock()
        self.speaker = FakeSpeaker()
        self.books = Books(self.root, speaker=self.speaker, clock=self.clock)
        self.addCleanup(self.books.pause)

    def test_pause_keeps_sentence_start(self) -> None:
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.waiting.wait(1))
        self.assertGreaterEqual(self.speaker.cancelled, 1)
        story = self.books.pause()
        self.assertFalse(story["playing"])
        self.assertEqual(story["stopped_by"], "user")
        self.assertEqual(story["offset"], story["sentence_start"])
        self.assertEqual(self.speaker.stops, 1)
        disk = json.loads((self.root / "progress.json").read_text(encoding="utf-8"))
        self.assertEqual(disk["books"]["雾海.txt"]["offset"], story["sentence_start"])

    def test_second_slice_waits_estimate(self) -> None:
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.waiting.wait(1))
        first = self.speaker.cued[0]
        self.clock.release()
        self.assertTrue(self.clock.waiting.wait(1))
        self.assertGreaterEqual(len(self.speaker.cued), 2)
        self.assertEqual(self.clock.now, estimate_read_seconds(first))
        self.books.pause()

    def test_end_then_play_restarts_at_zero(self) -> None:
        self.books.play("雾海.txt")
        for _ in range(12):
            if not self.clock.waiting.wait(1):
                break
            if not self.books.story()["playing"]:
                break
            self.clock.release()
        story = self.books.story()
        self.assertFalse(story["playing"])
        self.assertEqual(story["stopped_by"], "end")
        self.assertEqual(story["offset"], story["chars"])
        again = self.books.play("雾海.txt")
        self.assertTrue(again["playing"])
        self.assertEqual(again["sentence_start"], 0)
        self.books.pause()

    def test_cue_failure_stops_with_error(self) -> None:
        self.speaker.fail = True
        story = self.books.play("雾海.txt")
        self.assertFalse(story["playing"])
        self.assertEqual(story["stopped_by"], "error")
        self.assertTrue(story["error"])

    def test_bookmark_while_idle_does_not_speak(self) -> None:
        opened = self.books.open_book("雾海.txt")
        target = opened["chapters"][1]["start"]
        story = self.books.bookmark("雾海.txt", target)
        self.assertFalse(story["playing"])
        self.assertEqual(story["offset"], target)
        self.assertEqual(self.speaker.cued, [])
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.waiting.wait(1))
        with self.assertRaises(HubError) as caught:
            self.books.bookmark("雾海.txt", 0)
        self.assertEqual(caught.exception.status, 400)
        self.books.pause()

    def test_empty_book_cannot_play(self) -> None:
        (self.root / "空.txt").write_bytes(b"")
        with self.assertRaises(HubError) as caught:
            self.books.play("空.txt")
        self.assertEqual(caught.exception.message, "没有可以读的字")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && uv run python -m unittest tests.test_books.PlayTest -v`

Expected: FAIL，`play` 不存在。

- [ ] **Step 3: Write minimal implementation**

`Books` 增加 `speaker`、`clock`、`split_fn`。默认时钟用 `time.monotonic`、`time.time`，以及 `threading.Event.wait`。默认喇叭的三个方法是空操作，`cue` 返回 `True`。

播放线程不要在 `wait` 期间占着状态锁。送出前把书签写成 `sentence_start` 并落盘，同时更新内存里的 `sentence` / `sentence_start`。`wait` 返回 `elapsed` 后，读指针改成 `sentence_start + len(sentence)`，再取下一段。返回 `stopped` 时线程退出，书签留在段首。`speaker.cue` 返回 `False` 时 `stopped_by` 为 `error`，`error` 为「还没接上嘴巴」，书签留在这次要读的位置。

没有下一段时：`playing` 为 false，`stopped_by` 为 `end`，书签写成 `len(text)`，不要调用 `speaker.stop()`（最后一句已经等完）。再次 `play` 发现书签 `>= chars` 就从 0 开始。

`play` 开头调用 `speaker.cancel_short_reads()`。已经在读时再次 `play`：把读指针改到请求的 offset（缺省则用当前书签），设置跳转标记并 `interrupt.set()`，不要把这次当成新的一次暂停。线程看到跳转后清掉 interrupt，从新指针继续，不退出。

`pause`：`stopped_by = "user"`，`speaker.stop()`，`interrupt.set()`，等线程结束。没在读时直接返回 `story()`。

`bookmark`：正在读则 400。否则钳制偏移（超过末尾收到最后一个字；空书为 0），写入进度和 `current_id`。

`story()` 在没打开过书时：`playing` false，`book_id` 用进度里的 `current_id`，`offset` 用书签，章信息能从缓存或现拆得到就填，拆不了就让 `chapter` 为 `null`。`timer_*` 先按进度里的 `timer_min` 填，`timer_remaining_sec` 为 `null`。

空文档（拼接后没有可读字符，`next_slice` 一开始就是 `None`）在 `play` 里抛 400「没有可以读的字」，不要启动线程。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && uv run python -m unittest tests.test_books.PlayTest tests.test_books.SliceTest tests.test_books.SplitTest -v`

Expected: PASS。`test_end_then_play_restarts_at_zero` 若章数很少，12 次放行足够读完；没读完就让循环以 `playing` 变 false 为止，不要改成真睡。

- [ ] **Step 5: Commit**

跳过。

---

### Task 5: 定时

**Files:**
- Modify: `hub/dock_hub/books.py`
- Modify: `hub/tests/test_books.py`

**Interfaces:**
- Consumes: Task 4 的 `Books.play` / `pause` / `story`，`FakeClock`
- Produces: `Books.set_timer(minutes: int) -> dict`

- [ ] **Step 1: Write the failing test**

```python
class TimerTest(PlayTest):
    def test_deadline_during_wait_pauses_and_keeps_preference(self) -> None:
        self.books.set_timer(15)
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.waiting.wait(1))
        start = self.books.story()["sentence_start"]
        self.clock.now += 15 * 60
        self.clock.release()
        deadline = time.time() + 2
        while self.books.story()["playing"] and time.time() < deadline:
            time.sleep(0.01)
        story = self.books.story()
        self.assertFalse(story["playing"])
        self.assertEqual(story["stopped_by"], "timer")
        self.assertEqual(story["offset"], start)
        self.assertEqual(story["timer_min"], 15)
        self.assertIsNone(story["timer_remaining_sec"])
        self.assertGreaterEqual(self.speaker.stops, 1)

    def test_pause_freezes_remaining(self) -> None:
        self.books.set_timer(30)
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.waiting.wait(1))
        self.clock.now += 60
        story = self.books.pause()
        self.assertEqual(story["timer_remaining_sec"], 30 * 60 - 60)
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.waiting.wait(1))
        again = self.books.story()
        self.assertLess(again["timer_remaining_sec"], 30 * 60)
        self.assertGreater(again["timer_remaining_sec"], 30 * 60 - 120)
        self.books.pause()

    def test_jump_does_not_refill(self) -> None:
        opened = self.books.open_book("雾海.txt")
        self.books.set_timer(30)
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.waiting.wait(1))
        self.clock.now += 60
        before = self.books.story()["timer_remaining_sec"]
        self.books.play("雾海.txt", offset=opened["chapters"][-1]["start"])
        self.assertTrue(self.clock.waiting.wait(1))
        after = self.books.story()["timer_remaining_sec"]
        self.assertLessEqual(after, before)
        self.books.pause()

    def test_change_minutes_and_off(self) -> None:
        self.books.set_timer(30)
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.waiting.wait(1))
        changed = self.books.set_timer(15)
        self.assertEqual(changed["timer_remaining_sec"], 15 * 60)
        off = self.books.set_timer(0)
        self.assertIsNone(off["timer_remaining_sec"])
        self.assertTrue(off["playing"])
        with self.assertRaises(HubError):
            self.books.set_timer(10)
        self.books.pause()

    def test_next_play_after_timer_is_full_again(self) -> None:
        self.books.set_timer(15)
        self.books.play("雾海.txt")
        self.assertTrue(self.clock.waiting.wait(1))
        self.clock.now += 15 * 60
        self.clock.release()
        deadline = time.time() + 2
        while self.books.story()["playing"] and time.time() < deadline:
            time.sleep(0.01)
        story = self.books.play("雾海.txt")
        self.assertEqual(story["timer_remaining_sec"], 15 * 60)
        self.books.pause()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && uv run python -m unittest tests.test_books.TimerTest -v`

Expected: FAIL，`set_timer` 不存在，或到点后仍在读。

- [ ] **Step 3: Write minimal implementation**

内存里只保留一个截止时间 `deadline: float | None` 和一个冻住的剩余秒 `frozen: float | None`。

- `timer_min == 0`：两者都空，`timer_remaining_sec` 为 `null`。
- `play` 从停到开：有 `frozen` 就用 `now + frozen` 并清掉 `frozen`；否则 `timer_min > 0` 时 `deadline = now + timer_min * 60`。
- 已经在读时的 `play`（跳章、换书）不改 `deadline` / `frozen`。
- `pause`：若 `deadline` 存在，`frozen = max(0, deadline - now)`，再把 `deadline` 清空。
- `set_timer`：写入进度。正在读就把 `deadline` 改成 `now + minutes * 60`（`minutes == 0` 则清空）。暂停中有 `frozen` 或 `stopped_by == "user"` 且之前有定时，就把 `frozen` 改成新的整段秒数。完全空闲时只改偏好，不制造剩余秒。
- 读完或到点：`deadline` 和 `frozen` 都清空。到点时 `speaker.stop()`，`stopped_by` 为 `timer`，书签留在段首。
- `wait` 的 `deadline` 参数传当前截止时间。返回 `deadline`，或返回后 `monotonic() >= deadline`，都走至点路径。送下一段之前也先做同一次检查。

`timer_remaining_sec` 在正在读时是 `max(0, ceil(deadline - now))`；暂停且 `frozen` 有值时是 `ceil(frozen)`；否则 `null`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && uv run python -m unittest tests.test_books.TimerTest tests.test_books.PlayTest -v`

Expected: PASS

- [ ] **Step 5: Commit**

跳过。

---

### Task 6: 接到 Hub 的喇叭和 HTTP

**Files:**
- Modify: `hub/dock_hub/companion.py`（`stop` 约 380 行，`read` 约 418 行）
- Modify: `hub/dock_hub/service.py`（`__init__` 约 79 行，`_wire_companion` 约 385 行）
- Modify: `hub/dock_hub/server.py`（`do_GET` / `do_POST`，`_read_json` 附近）
- Modify: `hub/tests/test_books.py`
- Modify: `hub/tests/test_protocol.py`（`test_companion_disabled_is_404`）

**Interfaces:**
- Consumes: `Books.play`、`pause`、`story`、`bookmark`、`set_timer`、`list_books`、`save_upload`、`open_book`、`chapter`
- Produces: `Companion` 上的
  - `set_story_books(books) -> None`
  - `begin_story_audio() -> None` — 取消短稿并 ` _stop_tts`
  - `cue_story_sentence(text: str) -> bool`
  - `stop_story_audio() -> None` — 只停喇叭，不再调用 `books.pause`
  - `DockHub.books: Books`

`StorySpeaker` 的三个方法分别调用 `begin_story_audio`+`cue_story_sentence`、`stop_story_audio`、`begin_story_audio` 里的取消短稿部分。`cancel_short_reads` 必须能单独调用，避免 `cue` 每次都取消自己。

- [ ] **Step 1: Write the failing test**

在 `tests/test_books.py` 增加 `HttpTest`。`setUp` 复制 `tests/test_protocol.py` 的服务器搭法，配置里 `companion.enabled` 为 true，并带 `tts.base_url`。创建 `Books` 时传入 `FakeSpeaker` 和 `FakeClock`，然后同时赋值 `self.hub.books` 和 `self.hub.companion.set_story_books(self.hub.books)`。`_request` 复制协议测试的 JSON 请求。另写 `_raw` 发送原始字节。

```python
class HttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        cfg = parse_config(
            {
                "name": "study",
                "token": "secret-token-value",
                "host": "127.0.0.1",
                "port": 0,
                "path": str(root / "hub.yaml"),
                "devices": [],
                "companion": {
                    "enabled": True,
                    "tts": {"base_url": "http://127.0.0.1:9", "timeout_sec": 2},
                },
            }
        )
        self.hub = DockHub(cfg)
        self.clock = FakeClock()
        self.speaker = FakeSpeaker()
        self.hub.books = Books(root / "books", speaker=self.speaker, clock=self.clock)
        self.hub.companion.set_story_books(self.hub.books)
        (root / "books").mkdir(parents=True, exist_ok=True)
        (root / "books" / "已有.txt").write_bytes("海雾来了。".encode())
        handler = make_handler(self.hub)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.hub.books.pause)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _request(self, method, path, body=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        raw = None
        headers = {"Accept": "application/json"}
        if body is not None:
            raw = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=raw, headers=headers)
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode())
        conn.close()
        return resp.status, payload

    def test_lists_without_body(self) -> None:
        status, body = self._request("GET", "/v1/companion/books")
        self.assertEqual(status, 200)
        self.assertEqual(body["items"][0]["id"], "已有.txt")
        self.assertNotIn("text", body["items"][0])

    def test_upload_play_pause_and_bad_timer(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "POST",
            "/v1/companion/books",
            body="第一章 雾\n\n海雾来了。".encode(),
            headers={"X-Filename": "%E9%9B%BE%E6%B5%B7.txt", "Content-Type": "application/octet-stream"},
        )
        resp = conn.getresponse()
        uploaded = json.loads(resp.read().decode())
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertEqual(uploaded["id"], "雾海.txt")
        status, playing = self._request("POST", "/v1/companion/books/雾海.txt/play", {})
        self.assertEqual(status, 200)
        self.assertTrue(self.clock.waiting.wait(1))
        status, story = self._request("GET", "/v1/companion/story")
        self.assertTrue(story["playing"])
        self.assertEqual(self.hub.companion._read_jobs, {})
        status, paused = self._request("POST", "/v1/companion/books/雾海.txt/pause", {})
        self.assertFalse(paused["playing"])
        self.assertEqual(paused["stopped_by"], "user")
        status, bad = self._request("POST", "/v1/companion/story/timer", {"minutes": 10})
        self.assertEqual(status, 400)

    def test_short_read_pauses_story_and_keeps_bookmark(self) -> None:
        self.hub.books.save_upload("雾海.txt", "第一章 雾\n\n海雾来了。".encode())
        self._request("POST", "/v1/companion/books/雾海.txt/play", {})
        self.assertTrue(self.clock.waiting.wait(1))
        start = self.hub.books.story()["sentence_start"]
        self._request("POST", "/v1/companion/read", {"text": "短稿。", "mode": "play"})
        story = self.hub.books.story()
        self.assertFalse(story["playing"])
        self.assertEqual(story["offset"], start)
```

在 `tests/test_protocol.py` 的 `test_companion_disabled_is_404` 末尾加：

```python
status, body = self._request("GET", "/v1/companion/books")
self.assertEqual(status, 404)
self.assertEqual(body["error"]["code"], "not_found")
```

`parse_config` 若拒绝未知的 `path` 字段，就不要传 `path`，改让 `DockHub` 用它自己的配置路径；书架目录仍用测试临时目录，因为 `hub.books` 会被替换。短稿请求可以返回 502（测试里的 TTS 连不上），断言只看故事已经暂停。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && uv run python -m unittest tests.test_books.HttpTest tests.test_protocol.ProtocolTest.test_companion_disabled_is_404 -v`

Expected: FAIL，路由 404 或还没有 `hub.books`。

- [ ] **Step 3: Write minimal implementation**

`service.py` 在两处 `set_reads_dir` 旁边把书架目录设为同一父目录下的 `books`。`Books` 只创建一次；重新加载配置时若根目录变了，调用 `books.set_root(path)`，不要换掉测试替换过的实例。`Companion.set_story_books` 保存引用。

`Companion.stop` 在原有逻辑之前调用 `books.pause()`（书架再调用 `stop_story_audio` 会重复停一次喇叭，允许）。为避免递归：`pause()` 调的是 `stop_story_audio`，不是 `Companion.stop`。

`Companion.read` 在校验通过、入队之前调用 `books.pause()`，让短稿抢走喇叭时留下书签。

`begin_story_audio`：`_cancel_running_reads()`，然后持锁 `_seq += 1`、`_speaking = True`，再 `_stop_tts()`。`cue_story_sentence` 用这个新的 `_seq` 调现有 `_cue_sentence`。`stop_story_audio`：持锁 `_seq += 1`、`_speaking = False`，再 `_stop_tts()`。

`server.py` 增加 `_read_bytes(limit: int) -> bytes`，只给上传用，上限 `MAX_BOOK_BYTES`。超过则 400，不要读进内存超过上限（先看 `Content-Length`）。`X-Filename` 用 `unquote`。

路由与鉴权照 `_chats_ok`：

| 方法 | 路径 | 调用 |
| --- | --- | --- |
| GET | `/v1/companion/books` | `list_books` |
| GET | `/v1/companion/books/{id}` | `open_book` |
| GET | `/v1/companion/books/{id}/chapter/{index}` | `chapter` |
| GET | `/v1/companion/story` | `story` |
| POST | `/v1/companion/books` | `save_upload` |
| POST | `/v1/companion/books/{id}/play` | `play`，JSON 里可选 `offset` |
| POST | `/v1/companion/books/{id}/pause` | `pause` |
| POST | `/v1/companion/books/{id}/bookmark` | `bookmark` |
| POST | `/v1/companion/story/timer` | `set_timer` |

伴侣 `enabled` 为 false 时这些路由 404「未开启桌面伴侣」。`{id}` 先 `unquote` 再交给 `safe_book_id`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && uv run python -m unittest tests.test_books tests.test_protocol.ProtocolTest.test_companion_disabled_is_404 tests.test_companion -v`

Expected: PASS。原有短稿测试仍然通过。

- [ ] **Step 5: Commit**

跳过。

---

### Task 7: 朗读页

**Files:**
- Modify: `hub/dock_hub/assets/read.html`
- Modify: `hub/tests/test_protocol.py`（`test_companion_read_page` 已有，补断言）

**Interfaces:**
- Consumes: Task 6 的 HTTP 接口
- Produces: 页面上的故事视图。短稿控件 id 保持 `text`、`play`、`file`、`stop`、`import`。

- [ ] **Step 1: Write the failing test**

把 `test_companion_read_page` 的断言扩展为 HTML 同时包含 `id="story"`、`id="timer"`、`短稿`、原来的 `id="text"`。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && uv run python -m unittest tests.test_protocol.ProtocolTest.test_companion_read_page -v`

Expected: FAIL，缺少 `id="story"`。

- [ ] **Step 3: Write minimal implementation**

左边默认显示 `#story`：书名 `#bookTitle`、章名 `#chapterTitle`、进度 `#progressLabel`、正文 `#chapterBody`、按钮 `#storyPlay`、`#storyPause`、`#prevChapter`、`#nextChapter`、下拉 `#timer`（选项 value 为 `0`、`15`、`30`、`45`、`60`，文案「关 / 15 分钟 / 30 分钟 / 45 分钟 / 60 分钟」）、剩余时间 `#timerLeft`、状态 `#storyStatus`、导入仍用文件选择，但上传到 `POST /v1/companion/books`。按钮 `#showShort` 文案「短稿」，点开后隐藏 `#story`、显示原来的文本框和任务列表。

右边 `#toc` 渲染章节。`volume` 与上一章不同且非空时先插入一行卷名。`problem_label` 非空时放在目录上方 `#splitNote`。

打开页面：`GET /v1/companion/books` 与 `GET /v1/companion/story`。有 `current_id` 就 `GET /v1/companion/books/{id}`，再按 `story.chapter` 取章正文。不调用 `play`。

`#chapterBody` 用文本节点加一个 `<mark id="here">` 包住 `sentence_start` 相对该章 `start` 的切片，并 `scrollIntoView`。章下标显示 `index + 1` 与总章数，另加 `offset / chars` 的百分比。

正在读时点章节调用 `play` 且带上该章 `start`。没在读时调用 `bookmark`。上一章、下一章同样。`#storyPlay` 调用不带 offset 的 `play`。`#storyPause` 与页头 `#stop`：先 `POST /v1/companion/stop`（现有短稿停止），故事状态随后由轮询变成暂停。`#timer` 的 `change` 调用 `POST /v1/companion/story/timer`。

每秒和现有任务轮询一起 `GET /v1/companion/story`。`stopped_by === "timer"` 时 `#storyStatus` 为「到时间了，已停下」。`end` 时为「已读完」。正在读且 `timer_remaining_sec` 非空：大于等于 60 显示剩余分钟，否则显示剩余秒。暂停且剩余秒非空时也显示这串剩余时间。

焦点在 `#text` 里时不改现有快捷键。否则 `Space` 在播放与暂停间切换，`Esc` 走页头停止。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && uv run python -m unittest tests.test_protocol.ProtocolTest.test_companion_read_page -v`

Expected: PASS

- [ ] **Step 5: Commit**

跳过。

---

### Task 8: 协议说明与目录表

**Files:**
- Modify: `docs/lan-protocol.md`（路由表约 119 行，第 6.8 节约 612 行之后）
- Modify: `hub/README.md`（配置目录表约 33 行）
- Modify: `hub/tests/test_protocol.py`

**Interfaces:**
- Consumes: Task 6 的路径
- Produces: 文档里的路由说明

- [ ] **Step 1: Write the failing test**

```python
def test_lan_protocol_mentions_story_routes(self) -> None:
    text = Path(__file__).resolve().parents[2].joinpath("docs/lan-protocol.md").read_text(encoding="utf-8")
    self.assertIn("/v1/companion/books", text)
    self.assertIn("/v1/companion/story", text)
    self.assertIn("安卓不要打", text)
    readme = Path(__file__).resolve().parents[1].joinpath("README.md").read_text(encoding="utf-8")
    self.assertIn("books/", readme)
```

`parents[2]` 相对 `hub/tests/test_protocol.py` 才是仓库根。写测试前数一下：`test_protocol.py` 在 `hub/tests/`，仓库根是 `parents[2]`，`hub/README.md` 是 `parents[1]`。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && uv run python -m unittest tests.test_protocol.ProtocolTest.test_lan_protocol_mentions_story_routes -v`

Expected: FAIL，文档还没有这些路径。

- [ ] **Step 3: Write minimal implementation**

路由表加一行总览：`GET/POST /v1/companion/books` 与 `GET /v1/companion/story`，鉴权「是（本机可免）」，用途「睡前朗读。**安卓不要打**」。

在 6.8 节后加 6.9，写明：书在 Hub 配置目录 `books/`；`GET /v1/companion/books` 不返回正文；`POST` 上传用原始正文和 `X-Filename`，最大 32MB；打开、章节、播放、暂停、书签、`story`、`timer` 的路径与规格里的 JSON 字段一致；`minutes` 只能是 0、15、30、45、60；页头停止仍是 `POST /v1/companion/stop`，故事在读时同时留下书签。

`hub/README.md` 的表加一行：`~/.config/dock-hub/books/`，睡前朗读的 txt 和 `progress.json`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && uv run python -m unittest tests.test_books tests.test_protocol tests.test_companion -v`

Expected: PASS

- [ ] **Step 5: Commit**

跳过。
