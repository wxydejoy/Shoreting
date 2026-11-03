"""OpenAI 兼容 /v1/audio/speech 端点单测（T4）。

不加载真模型：make_handler 收一个鸭子类型的 FakeEngine，专门打这个端点的行为。
"""

from __future__ import annotations

import io
import json
import threading
import unittest
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer

from server import make_handler


class FakeNoise:
    def status(self) -> dict:
        return {"playing": False, "name": None, "seconds_left": 0, "minutes": 0}


class FakeEngine:
    """只实现 /v1/audio/speech 触碰到的面。"""

    def __init__(self, *, ready: bool = True, interrupt: bool = False):
        self.ready_flag = ready
        self.interrupt = interrupt
        self.error: str | None = None
        self.play = False
        self.speed = 1.0
        self.calls: list[dict] = []

    def ready(self) -> bool:
        return self.ready_flag

    def speak(self, text, voice, language, expected_gen=None, allow_play=None, speed=None):
        self.calls.append({"text": text, "voice": voice, "language": language, "allow_play": allow_play, "speed": speed})
        if self.interrupt:
            raise InterruptedError("stopped")
        return WAV_FIXTURE, 123


def make_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(b"\x00\x01" * 100)
    return buffer.getvalue()


WAV_FIXTURE = make_wav()


def pcm_of(wav: bytes) -> bytes:
    with wave.open(io.BytesIO(wav)) as reader:
        return reader.readframes(reader.getnframes())


class EndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = FakeEngine()
        handler = make_handler(cls.engine, FakeNoise(), token="secret-token")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, body: dict, *, auth: bool = True, method: str = "POST"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/audio/speech",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}
            | ({"Authorization": "Bearer secret-token"} if auth else {}),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_wav_passthrough(self):
        status, headers, payload = self.request({"input": "晚上好，漂泊者。"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "audio/wav")
        self.assertEqual(payload, WAV_FIXTURE)
        self.assertEqual(headers.get("X-First-Audio-Ms"), "123")
        call = self.engine.calls[-1]
        self.assertEqual(call["allow_play"], False)  # 只回字节不出声
        self.assertIsNone(call["voice"])  # 克隆模式下 voice 交给引擎处理

    def test_pcm_strips_header(self):
        status, headers, payload = self.request({"input": "读两页再睡。", "response_format": "pcm"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "audio/pcm")
        self.assertEqual(payload, pcm_of(WAV_FIXTURE))
        self.assertEqual(len(payload), 200)  # 100 帧 × 2 字节

    def test_empty_input_rejected(self):
        status, _, payload = self.request({"input": "   "})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "bad_request")

    def test_unsupported_format_rejected(self):
        status, _, payload = self.request({"input": "你好", "response_format": "mp3"})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "unsupported_format")

    def test_speed_clamped_at_route(self):
        self.request({"input": "慢一点。", "speed": 3.0})
        # 与 /v1/speak 一致：路由层先钳位（0.7–1.5）再转发
        self.assertEqual(self.engine.calls[-1]["speed"], 1.5)

    def test_not_ready_503(self):
        self.engine.ready_flag = False
        status, _, payload = self.request({"input": "你好"})
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(payload)["error"]["code"], "not_ready")
        self.engine.ready_flag = True

    def test_interrupted_maps_to_stopped(self):
        self.engine.interrupt = True
        status, _, payload = self.request({"input": "你好"})
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(payload)["error"]["code"], "stopped")
        self.engine.interrupt = False

    def test_requires_bearer_token(self):
        status, _, _ = self.request({"input": "你好"}, auth=False)
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
