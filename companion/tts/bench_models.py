"""对比不同量化档的合成速度：与 server.py 完全同参数。

用法: python bench_models.py <model_dir> ...
每个模型跑：1 次短句预热 + 3 段文本（流式测首块延迟，非流式测 RTF）。
"""
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
REF_AUDIO = str(ROOT / "voices/shorekeeper/clone.wav")
REF_TEXT = (ROOT / "voices/shorekeeper/clone.lab").read_text(encoding="utf-8").strip()

TEXTS = [
    "嗯，好的。",
    "今晚八点我帮你把这一周的情况总结一下，别忘了看一下。",
    "现在为你读今天下午的安排：三点有一场设计评审，五点记得取快递，路上小心。",
]


def audio_seconds(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate()


def bench(model_dir: str) -> None:
    from mlx_audio.tts.utils import load_model

    t0 = time.monotonic()
    model = load_model(model_dir)
    print(f"[{Path(model_dir).name}] 加载 {time.monotonic() - t0:.1f}s", flush=True)

    for i, text in enumerate(TEXTS):
        # 流式（线上播放同款）：测首块
        t0 = time.monotonic()
        first_ms = 0
        for result in model.generate(
            text=text,
            lang_code="chinese",
            ref_audio=REF_AUDIO,
            ref_text=REF_TEXT,
            stream=True,
            streaming_interval=0.4,
            verbose=False,
        ):
            if not first_ms:
                first_ms = int((time.monotonic() - t0) * 1000)
        stream_total = (time.monotonic() - t0) * 1000

        # 非流式：测 RTF
        t0 = time.monotonic()
        rate = 24000
        samples = 0
        for result in model.generate(
            text=text,
            lang_code="chinese",
            ref_audio=REF_AUDIO,
            ref_text=REF_TEXT,
            verbose=False,
        ):
            rate = int(getattr(result, "sample_rate", rate) or rate)
            samples += int(np.asarray(result.audio).shape[0])
        gen_s = time.monotonic() - t0
        dur = samples / rate
        chars = len(text)
        print(
            f"  {chars:>2}字 首块 {first_ms:>5}ms  流式总 {stream_total/1000:.1f}s  "
            f"音频 {dur:.1f}s  生成 {gen_s:.1f}s  RTF {gen_s/dur:.2f}",
            flush=True,
        )


if __name__ == "__main__":
    for m in sys.argv[1:]:
        bench(m)
