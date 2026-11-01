package cn.weiekko.dock.voice

/**
 * 断句的时间状态机——「什么时候算说完」的判断全部来自 VAD，这里只管时序：
 *
 * - VAD 弹出语音段 = 「人声 + 尾静音」凑齐（pipecat 的 stop_secs 到点）；
 * - 段弹出后还要等 ASR 收尾：pipecat 的 stop strategy 有 wait_for_transcript，
 *   静音尾巴里转写还在长新字，说明那几个字还没识别完，不能急着断——
 *   「关电脑」的「电脑」就常在这时才落进转写，等它 500ms 再断；
 * - 一直没人声 = Empty，说到上限 = Max。
 *
 * 不做任何音量/内容判断：声音是不是人声是 [SpeechVad] 的事，
 * 说了什么是识别器的事。
 */
internal class VadTurnGate(
    private val emptyMs: Long = 6_500L,
    private val maxMs: Long = 16_000L,
    private val transcriptSettleMs: Long = 500L,
) {
    var heard = false
        private set
    private var segmentAt = -1L
    private var lastTextAt = -1L

    /** VAD 报告这帧有人声。 */
    fun onSpeech(elapsedMs: Long) {
        heard = true
    }

    /** 转写长出了新字。 */
    fun onText(elapsedMs: Long) {
        heard = true
        lastTextAt = elapsedMs
    }

    /** VAD 弹出语音段：断句提议就位。 */
    fun onSegment(elapsedMs: Long) {
        heard = true
        if (segmentAt < 0) segmentAt = elapsedMs
    }

    fun shouldStop(elapsedMs: Long): StopReason? {
        if (elapsedMs >= maxMs) return StopReason.Max
        if (segmentAt >= 0 && elapsedMs - maxOf(segmentAt, lastTextAt) >= transcriptSettleMs) {
            return StopReason.Tail
        }
        if (!heard && elapsedMs >= emptyMs) return StopReason.Empty
        return null
    }

    enum class StopReason { Tail, Empty, Max }
}
