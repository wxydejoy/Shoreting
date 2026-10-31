package cn.weiekko.dock.voice

/**
 * 流式识别每 50ms 把整句重算一遍，尾巴偶尔会往回缩（「几点了」→「几点」），
 * 字幕跟着跳一下，看着像在抽搐。而且每变一次就发一次广播，主屏一句十几个来回。
 *
 * 这里做两件事：
 *  - 只往后长：比已经显示过的还短就当噪音丢掉，等它自己长回来
 *  - 节流：没多出两个字、又没到间隔，先不发
 *
 * 最终结果不走这里：`settled = true` 那一次永远照发，长短都算数。
 */
internal class StablePartial(
    private val minGapMs: Long = 110L,
    private val growChars: Int = 2,
) {
    private var shown = ""
    private var shownAt = -1L

    /** 拿一句新的半成品问问要不要显示；返回 null 就是这次不发。 */
    fun offer(text: String, atMs: Long): String? {
        val next = text.trim()
        if (next.isEmpty() || next == shown) return null
        // 往回缩：大概率是这一拍解码错了，等它长回来
        if (next.length < shown.length) return null
        val gap = if (shownAt < 0L) Long.MAX_VALUE else atMs - shownAt
        if (gap < minGapMs && next.length - shown.length < growChars) return null
        shown = next
        shownAt = atMs
        return next
    }

    fun reset() {
        shown = ""
        shownAt = -1L
    }
}
