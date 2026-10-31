package cn.weiekko.dock.voice

/**
 * 免唤醒连续对话（F1）：喊一次「岸宝」，接下来一段时间内直接说话就能接上。
 *
 * 窗口规则（features.md F1）：
 * - 她每说完一句开一个窗口：问句给 15 秒（等着你回答），陈述句给 8 秒——
 *   说完「好。」不该还等你没完；
 * - 窗口内连着两轮都没听出人话就关窗；有效输入把连击清零；
 * - 她说话的几秒里窗口不算数（麦克风听到的是她自己），所以「开窗」
 *   从回复落定、按字数估完 TTS 时长之后才开始。
 *
 * 纯逻辑，时间由调用方喂（`HelmLatency.now()`，即 elapsedRealtime），
 * 单元测试用假时刻验，不需要真等 15 秒。
 */
internal class ConversationWindow(
    private val questionMs: Long = QUESTION_MS,
    private val statementMs: Long = STATEMENT_MS,
    private val maxMissStreak: Int = MAX_MISS_STREAK,
) {
    private var until = 0L
    private var startAt = 0L
    private var misses = 0

    /** 她说完一句：返回「等她说完要多少毫秒」，窗口从那之后才开。 */
    fun onReply(text: String, nowMs: Long): Long {
        val wait = speakMs(text.length)
        val span = if (CompanionFollowUp.asks(text)) questionMs else statementMs
        startAt = nowMs + wait
        until = startAt + span
        misses = 0
        return wait
    }

    /** 一轮听完没有有效输入（空转 / 没听清）。窗口没开就当没这回事。 */
    fun onMiss(nowMs: Long) {
        if (!listening(nowMs)) return
        misses += 1
        if (misses >= maxMissStreak) until = 0L
    }

    /** 窗口内听到了人话，连击清零。 */
    fun onInput(nowMs: Long) {
        if (!listening(nowMs)) return
        misses = 0
    }

    fun close() {
        until = 0L
        startAt = 0L
        misses = 0
    }

    fun listening(nowMs: Long): Boolean = nowMs >= startAt && nowMs < until

    /** 窗口到点时刻，给 ConversationBus 与 UI 呼吸光用。 */
    fun listeningUntil(): Long = until

    companion object {
        const val QUESTION_MS = 15_000L
        const val STATEMENT_MS = 8_000L
        const val MAX_MISS_STREAK = 2
        private const val MS_PER_CHAR = 260L
        private const val TAIL_MS = 1_500L
        private const val SPEAK_CAP_MS = 20_000L

        /** TTS 大约 4 字/秒，回复越长等得越久；封顶 20 秒防止长篇朗读把窗口饿死。 */
        fun speakMs(chars: Int): Long =
            (chars * MS_PER_CHAR + TAIL_MS).coerceAtMost(SPEAK_CAP_MS)
    }
}

/**
 * ViewModel 与 WakeWordService 之间的窗口桥：VM 持有对话窗口的规则与连击，
 * 服务只看「现在该不该跳过唤醒词」。suppress 是她说话的那几秒，窗口再开也不听。
 */
object ConversationBus {
    @Volatile
    private var openUntilAt = 0L

    @Volatile
    private var suppressUntilAt = 0L

    fun openUntil(atMs: Long) {
        if (atMs > openUntilAt) openUntilAt = atMs
    }

    fun suppressUntil(atMs: Long) {
        if (atMs > suppressUntilAt) suppressUntilAt = atMs
    }

    fun close() {
        openUntilAt = 0L
        suppressUntilAt = 0L
    }

    fun bypass(nowMs: Long): Boolean = nowMs < openUntilAt && nowMs >= suppressUntilAt

    /** 她正在说话（TTS 估算的时段）；窗口再开也不听，但可以插话（F2）。 */
    fun speaking(nowMs: Long): Boolean = nowMs < suppressUntilAt
}
