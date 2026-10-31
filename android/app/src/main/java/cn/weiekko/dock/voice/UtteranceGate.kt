package cn.weiekko.dock.voice

import kotlin.math.sqrt

/**
 * 用音量判断一句什么时候算说完。
 * sherpa 的 CTC 端点在这套模型上经常拖到上限才停，所以听感要靠这个。
 */
internal class UtteranceGate(
    private val endSilenceMs: Long = 1_000L,
    private val emptyMs: Long = 6_500L,
    private val maxMs: Long = 16_000L,
    private val minTailMs: Long = 1_500L,
    private val minSpeechMs: Long = 140L,
    private val calibrateMs: Long = 280L,
) {
    var heard = false
        private set
    private var speechRunMs = 0L
    private var silenceMs = 0L
    private var calibratedMs = 0L
    private var noise = 0.012f

    /**
     * 只标记「听到了人声」，**不动静音计时**。
     *
     * 这里千万别再 `silenceMs = 0`：识别器的结果在静音时不会清空，调用方每 50ms
     * 的音频块都会看到非空文本进而调到这里，静音计时就被无限清零，尾静音永远
     * 凑不满，每句话都要等到 16s 上限才停（真机日志：`asr stop Max elapsed=16000`，
     * 唤醒到说话整整 17 秒）。计时归零只归 [onAudio] 管，它看的是音量。
     */
    fun noteHeard() {
        heard = true
        speechRunMs = minSpeechMs
    }

    fun onAudio(rms: Float, dtMs: Long) {
        if (dtMs <= 0L) return
        if (calibratedMs < calibrateMs) {
            calibratedMs += dtMs
            noise = noise * 0.72f + rms * 0.28f
            if (rms < 0.06f) return
        }
        val speechRms = (noise * 3.4f).coerceIn(0.014f, 0.048f)
        val silenceRms = (noise * 1.45f).coerceIn(0.006f, 0.016f)
        when {
            rms >= speechRms -> {
                speechRunMs += dtMs
                silenceMs = 0L
                if (speechRunMs >= minSpeechMs) heard = true
            }
            rms <= silenceRms -> {
                speechRunMs = 0L
                silenceMs += dtMs
            }
            else -> {
                // 两字之间音量掉一点，不要算成说完
                speechRunMs = 0L
            }
        }
    }

    fun shouldStop(elapsedMs: Long): StopReason? {
        if (elapsedMs >= maxMs) return StopReason.Max
        if (heard && elapsedMs >= minTailMs && silenceMs >= endSilenceMs) return StopReason.Tail
        if (!heard && elapsedMs >= emptyMs) return StopReason.Empty
        return null
    }

    enum class StopReason { Tail, Empty, Max, Endpoint }
}

internal fun audioRms(samples: FloatArray): Float {
    if (samples.isEmpty()) return 0f
    var sum = 0.0
    for (sample in samples) sum += sample * sample
    return sqrt(sum / samples.size).toFloat()
}
