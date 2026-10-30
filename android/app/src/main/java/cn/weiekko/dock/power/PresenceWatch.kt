package cn.weiekko.dock.power

/**
 * 在场判定（F24）：摄像头只产出「有人 / 没人」的布尔值，这里把它变成
 * 「坐下 / 离座」的状态。纯逻辑、不碰任何 Android API，手动拨钟可单测。
 *
 * 规矩：
 * - 一帧有人就算坐下（亮屏要跟手，宁可快）；
 * - 连续没人满 [awayAfterMs] 才算离座（门口取快递不熄屏）；
 * - 回座边沿把这次离座了多久一起报出去，问候与否交给上层。
 */
class PresenceWatch(
    private val awayAfterMs: Long = AWAY_AFTER_MS,
    private val onPresent: (awayMs: Long) -> Unit = {},
    private val onAway: () -> Unit = {},
) {
    private var present = false
    private var everPresent = false
    private var lastSeenAt = 0L

    val isPresent: Boolean get() = present

    /** 离座持续了多久；在座恒为 0。 */
    fun awayFor(nowMs: Long): Long = if (present) 0L else (nowMs - lastSeenAt).coerceAtLeast(0L)

    /** 每一帧检测结果喂进来，person = 这帧里有没有人。 */
    fun onFrame(person: Boolean, nowMs: Long) {
        if (person) {
            val gap = if (everPresent) nowMs - lastSeenAt else 0L
            lastSeenAt = nowMs
            if (!present) {
                present = true
                everPresent = true
                onPresent(gap)
            }
        } else if (present && nowMs - lastSeenAt >= awayAfterMs) {
            present = false
            onAway()
        }
    }

    /**
     * 人已经出了声 / 碰了屏：直接算在场，但不发问候边沿——她正在说话，
     * 不缺这一句。摄像头看见的下一帧照常续走。
     */
    fun noteActivity(nowMs: Long) {
        lastSeenAt = nowMs
        present = true
        everPresent = true
    }

    companion object {
        /** 离座多久算走了：plan 里写死 5 分钟。 */
        const val AWAY_AFTER_MS = 5 * 60_000L
    }
}
