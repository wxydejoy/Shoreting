package cn.weiekko.dock.voice

/**
 * `AudioRecord.read` 的返回值为负时代表这块缓冲已经不能用了
 * （ERROR_INVALID_OPERATION / ERROR_DEAD_OBJECT：别的应用抢走了麦克风、
 * 或者设备休眠后驱动重挂）。为 0 只表示这一拍没数据。
 *
 * 原来两种情况都是 `continue`：负值会在原地打成死循环，一个核跑满 100%、
 * 麦克风却再也没有数据，唤醒词从此聋掉而且没人知道。这里把它拆开：
 * 前几次退避等一下（抢占往往是暂时的），连续失败就重开录音器；
 * 重开也不至于挤在一起（[minReopenGapMs] 是两次重开之间至少的间隔）。
 */
internal sealed class MicAction {
    /** 等这么多毫秒再读下一拍。 */
    data class Wait(val ms: Long) : MicAction()

    /** 把 AudioRecord 收掉重建。 */
    object Reopen : MicAction()
}

internal class MicFaults(
    private val reopenAfter: Int = 3,
    private val firstWaitMs: Long = 60L,
    private val maxWaitMs: Long = 1_500L,
    private val minReopenGapMs: Long = 2_000L,
) {
    private var consecutive = 0
    private var lastReopenAt = 0L

    var reopens = 0
        private set

    /** 录音器刚开好（或刚重开），从零开始数。 */
    fun opened() {
        consecutive = 0
    }

    /** 读到数据了，之前的失败不算数。 */
    fun data() {
        consecutive = 0
    }

    fun onError(atMs: Long): MicAction {
        consecutive += 1
        if (consecutive >= reopenAfter) {
            val since = atMs - lastReopenAt
            if (since >= minReopenGapMs) {
                consecutive = 0
                lastReopenAt = atMs
                reopens += 1
                return MicAction.Reopen
            }
            return MicAction.Wait((minReopenGapMs - since).coerceAtMost(maxWaitMs))
        }
        val step = firstWaitMs shl (consecutive - 1).coerceIn(0, 5)
        return MicAction.Wait(step.coerceAtMost(maxWaitMs))
    }
}
