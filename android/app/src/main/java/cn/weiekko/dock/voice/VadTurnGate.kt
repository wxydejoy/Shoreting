package cn.weiekko.dock.voice

/**
 * 断句的时间状态机——「什么时候算说完」的判断全部来自 VAD，这里只管时序：
 *
 * - VAD 弹出语音段 = 「人声 + 尾静音」凑齐（pipecat 的 stop_secs 到点）；
 * - 段弹出后还要等 ASR 收尾：pipecat 的 stop strategy 有 wait_for_transcript，
 *   静音尾巴里转写还在长新字，说明那几个字还没识别完，不能急着断——
 *   「关电脑」的「电脑」就常在这时才落进转写，等它 500ms 再断；
 * - VAD 整轮没弹段但转写有字（远场小声，模型概率过不了 0.5 线的失明形态），
 *   退而求其次按「转写停止长字」断——pipecat 的 transcription-stability
 *   endpoint 同思路：文字 1.8s 不长新字、VAD 也没报人声，就当说完了。
 *   不兜这层的话这种轮次只能干等 16s 的 Max；
 * - 一直没人声 = Empty，说到上限 = Max。
 *
 * 不做任何音量/内容判断：声音是不是人声是 [SpeechVad] 的事，
 * 说了什么是识别器的事。
 */
internal class VadTurnGate(
    private val emptyMs: Long = 6_500L,
    private val maxMs: Long = 16_000L,
    private val transcriptSettleMs: Long = 500L,
    private val textQuietMs: Long = 1_800L,
) {
    var heard = false
        private set
    private var segmentAt = -1L
    private var lastTextAt = -1L
    private var lastVadSpeechAt = -1L

    /** VAD 报告这帧有人声。 */
    fun onSpeech(elapsedMs: Long) {
        heard = true
        lastVadSpeechAt = elapsedMs
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
        // VAD 失明后备：文字和人声都安静够久（人声那侧要是最近还在报，
        // 说明 VAD 活着且用户在说，交给上面的段路径，不许这里抢断）
        if (heard && elapsedMs - maxOf(lastTextAt, lastVadSpeechAt) >= textQuietMs) {
            return StopReason.TextTail
        }
        if (!heard && elapsedMs >= emptyMs) return StopReason.Empty
        return null
    }

    enum class StopReason { Tail, TextTail, Empty, Max }
}
