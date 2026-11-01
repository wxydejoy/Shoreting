package cn.weiekko.dock.voice

import android.content.res.AssetManager
import android.util.Log
import com.k2fsa.sherpa.onnx.SpeechSegment
import com.k2fsa.sherpa.onnx.TenVadModelConfig
import com.k2fsa.sherpa.onnx.Vad
import com.k2fsa.sherpa.onnx.VadModelConfig

/**
 * 断句不再自己拿音量阈值猜（老 UtteranceGate 的 RMS 门，声音一小就当说完了），
 * 换成 sherpa 自带的 TEN VAD——pipecat / LiveKit 这类框架的通行做法：
 * 神经网络逐帧判断「这是不是人声」，离得远、声音小也认得出，
 * 字与字之间的短停顿不会被当成一句说完了。
 *
 * 参数取各家默认值 / 调优区间的交集（不是拍脑袋）：
 * - threshold 0.5：TEN VAD 默认判定线。
 * - minSilence 0.7s：静音多久才算「这句说完了」。pipecat 的调优区间 0.3–0.5s，
 *   LiveKit 默认 0.6s；摆件是远场拾音，取偏保守的 0.7。
 * - minSpeech 0.25s：比这短的「人声」按噪声丢（咳嗽、碰桌子）。
 * - maxSpeechDuration 20s：服务层 16s 的 Max 上限先到，这里只是兜底。
 *
 * 模型装不上（文件缺失之类）返回 null，调用方退回老的 RMS 门，语音不至于哑掉。
 */
internal class SpeechVad private constructor(private val vad: Vad) {

    private var lastProbLogAt = 0L

    fun feed(samples: FloatArray) {
        vad.acceptWaveform(samples)
        // 调试：每秒报一次状态。注意别在这里调 vad.compute()——那会再跑一遍
        // 模型、把 TEN 的隐状态往前推一格，VAD 就被自己污染了
        val now = android.os.SystemClock.elapsedRealtime()
        if (now - lastProbLogAt >= 1_000) {
            lastProbLogAt = now
            Log.d(TAG, "speech=%s segQueued=%s".format(vad.isSpeechDetected(), !vad.empty()))
        }
    }

    /** 吃进这帧之后，VAD 是否处于「正在说话」状态。 */
    fun speechActive(): Boolean = vad.isSpeechDetected()

    /** 「人声 + 足够长的尾静音」凑齐就弹出一个段；弹出即断句提议。 */
    fun pollSegment(): SpeechSegment? {
        if (vad.empty()) return null
        val segment = vad.front()
        vad.pop()
        return segment
    }

    fun reset() {
        vad.clear()
        vad.reset()
    }

    fun release() {
        vad.release()
    }

    companion object {
        private const val TAG = "SpeechVad"
        private const val MODEL = "vad/ten-vad.onnx"
        private const val THRESHOLD = 0.5f
        private const val MIN_SILENCE_S = 0.7f
        private const val MIN_SPEECH_S = 0.25f
        private const val MAX_SPEECH_S = 20f

        /** TEN VAD 优化窗长：256 采样 @16kHz = 16ms。 */
        private const val TEN_WINDOW = 256

        fun create(assets: AssetManager): SpeechVad? {
            return runCatching {
                val config = VadModelConfig(
                    tenVadModelConfig = TenVadModelConfig(
                        model = MODEL,
                        threshold = THRESHOLD,
                        minSilenceDuration = MIN_SILENCE_S,
                        minSpeechDuration = MIN_SPEECH_S,
                        windowSize = TEN_WINDOW,
                        maxSpeechDuration = MAX_SPEECH_S,
                    ),
                    sampleRate = 16_000,
                    numThreads = 1,
                    provider = "cpu",
                    debug = false,
                )
                SpeechVad(Vad(assets, config))
            }.onFailure { Log.w(TAG, "ten vad unavailable, rms fallback", it) }.getOrNull()
        }
    }
}
