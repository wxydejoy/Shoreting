package cn.weiekko.dock.ui

import java.time.LocalDateTime

/**
 * 防烧屏（F38）：K20 是常亮摆件，时钟和数据块长时间停在同一像素上必然留影。
 *
 * 三件事，全部纯函数，时间驱动，不碰网络：
 * 1. 时钟漂移：每 5 分钟在 ±2px 内挪一步，绕中心一圈后归零；
 * 2. 整体偏移：画面静止 30 分钟后，整个模块层挪 2–3px，再过 30 分钟挪回；
 * 3. 夜间降亮：23:00–07:00 整屏压到 40% 亮度（由调用方叠黑色遮罩实现）。
 */
data class PixelShift(val x: Int, val y: Int)

object BurnGuard {
    /** 相邻两步位移都不超过 2px；第 9 步回到原点，循环无跳变。 */
    val DRIFT_PATTERN = listOf(
        PixelShift(0, 0),
        PixelShift(2, 1),
        PixelShift(1, 2),
        PixelShift(-1, 2),
        PixelShift(-2, 1),
        PixelShift(-2, -1),
        PixelShift(-1, -2),
        PixelShift(1, -2),
        PixelShift(2, -1),
    )

    /** 整体偏移走四个角落，单步 2–3px，四步一循环。 */
    val STATIC_PATTERN = listOf(
        PixelShift(0, 0),
        PixelShift(3, 2),
        PixelShift(2, -3),
        PixelShift(-3, 1),
    )

    const val DRIFT_STEP_MINUTES = 5L
    const val STATIC_HOLD_MINUTES = 30L
    const val NIGHT_DIM_ALPHA = 0.40f

    /** 时钟漂移：每 [stepMinutes] 分钟走一步 [DRIFT_PATTERN]。负数按 0 处理。 */
    fun driftShift(elapsedMinutes: Long, stepMinutes: Long = DRIFT_STEP_MINUTES): PixelShift {
        if (elapsedMinutes <= 0) return DRIFT_PATTERN[0]
        val steps = elapsedMinutes / stepMinutes
        return DRIFT_PATTERN[(steps % DRIFT_PATTERN.size).toInt()]
    }

    /** 整体偏移：不满 [holdMinutes] 分钟不动，之后每满一轮换一个角。 */
    fun staticShift(elapsedMinutes: Long, holdMinutes: Long = STATIC_HOLD_MINUTES): PixelShift {
        if (elapsedMinutes < holdMinutes) return STATIC_PATTERN[0]
        val steps = elapsedMinutes / holdMinutes
        return STATIC_PATTERN[(steps % STATIC_PATTERN.size).toInt()]
    }

    /** 夜间降亮系数：23:00–06:59 返回 [NIGHT_DIM_ALPHA]，其余返回 1。 */
    fun nightDimFactor(now: LocalDateTime): Float =
        if (now.hour >= 23 || now.hour < 7) NIGHT_DIM_ALPHA else 1f
}
