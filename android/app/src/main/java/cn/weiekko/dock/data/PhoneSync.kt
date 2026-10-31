package cn.weiekko.dock.data

/**
 * 控制台改了手机端外观 / 电源 / 天气之后，手机这边要做的事。
 *
 * 只做一件事：把 snapshot.phone 翻成一组**待写入的 DataStore 字段**。写不写、
 * 什么时候写由 DockViewModel 决定，这里保持纯函数，好让单测直接对着边界值敲。
 *
 * 两条规矩：
 * - `rev <= appliedRev` 一律不动。这也是「手机上刚手改完，下一轮轮询别把值顶
 *   回去」的实现方式：只有控制台真的保存过（Hub 那边 rev +1）才覆盖本地。
 * - 数值在这里再 coerce 一次。Hub 已经拦过越界，但旧版 Hub / 手写 yaml 都可能
 *   给出奇怪的数，宁可夹到边上也不要崩在渲染里。
 */
data class PhonePatch(
    val tile: TileLook? = null,
    val type: TypeLook? = null,
    val powerScreen: Boolean? = null,
    val sleepSec: Int? = null,
    val weatherEnabled: Boolean? = null,
    val weatherCity: String? = null,
)

object PhoneSync {
    /** 城市名最长留这么多字：够写「内蒙古自治区呼和浩特市」，再长多半是粘错了。 */
    const val MAX_CITY = 24

    /** 与 HubPreferences.saveHubSleepDelaySec 的 coerceIn 保持一致。 */
    const val MIN_SLEEP_SEC = 15
    const val MAX_SLEEP_SEC = 600

    /** 要不要动、动哪些。null 表示这一轮什么都不用做。 */
    fun plan(remote: PhoneSettings?, appliedRev: Int): PhonePatch? {
        if (remote == null) return null
        if (remote.rev <= appliedRev) return null
        val city = remote.weather.city.trim().take(MAX_CITY)
        return PhonePatch(
            tile = TileLook(
                style = TileStyle.fromId(remote.tile.style),
                opacityPercent = remote.tile.opacity
                    .coerceIn(TileLook.OPACITY_MIN, TileLook.OPACITY_MAX),
                cornerDp = remote.tile.corner.coerceIn(TileLook.CORNER_MIN, TileLook.CORNER_MAX),
            ),
            type = TypeLook(
                font = DockFont.fromId(remote.type.font),
                clockScalePercent = remote.type.clock
                    .coerceIn(TypeLook.CLOCK_MIN, TypeLook.CLOCK_MAX),
                statsSize = remote.type.stats.coerceIn(TypeLook.STATS_MIN, TypeLook.STATS_MAX),
                tileSize = remote.type.tile.coerceIn(TypeLook.TILE_MIN, TypeLook.TILE_MAX),
                chipSize = remote.type.chip.coerceIn(TypeLook.CHIP_MIN, TypeLook.CHIP_MAX),
            ),
            powerScreen = remote.screen.keepOn,
            sleepSec = remote.screen.sleepSec.coerceIn(MIN_SLEEP_SEC, MAX_SLEEP_SEC),
            weatherEnabled = remote.weather.enabled,
            // 空城市名不写：saveWeatherCity 本来也会忽略，这里显式给 null 免得白跑一趟
            weatherCity = city.ifEmpty { null },
        )
    }

    /** 套用之后本地该记成哪一版。rev 只增不减，来回跳号不乱序。 */
    fun nextRev(remote: PhoneSettings?, appliedRev: Int): Int {
        val rev = remote?.rev ?: 0
        return if (rev > appliedRev) rev else appliedRev
    }

    /** 与 DockLayout.isPlaceholder 的判定一致：w/h 低于它表示「位置用默认值」。 */
    const val PLACEHOLDER_MIN = 0.05f

    /**
     * 布局走独立的 rev（appliedLayoutRev）：保存布局不该顺带把外观顶一遍。
     * 返回 null 表示这轮布局不用动；否则给一份要整体写入 DataStore 的用户布局
     * （就是「改过的槽位」那份，不是合并后的完整布局——渲染时仍会和默认值合并）。
     */
    fun layoutPlan(remote: PhoneSettings?, appliedLayoutRev: Int): DockLayout? {
        val layout = remote?.layout ?: return null
        if (layout.rev <= appliedLayoutRev) return null
        val modules = layout.modules.mapValues { (_, rect) ->
            val w = rect.w.coerceIn(0f, 1f)
            val h = rect.h.coerceIn(0f, 1f)
            if (w < PLACEHOLDER_MIN || h < PLACEHOLDER_MIN) {
                // 占位矩形：位置归零，渲染时用默认位置，只认显隐和边框
                ModuleRect(
                    x = 0f, y = 0f, w = 0f, h = 0f,
                    visible = rect.visible, chrome = rect.chrome,
                )
            } else {
                ModuleRect(
                    x = rect.x.coerceIn(0f, 1f),
                    y = rect.y.coerceIn(0f, 1f),
                    w = w, h = h,
                    visible = rect.visible, chrome = rect.chrome,
                )
            }
        }
        return DockLayout(modules = modules)
    }

    /** 布局套用之后本地该记成哪一版。 */
    fun nextLayoutRev(remote: PhoneSettings?, appliedLayoutRev: Int): Int {
        val rev = remote?.layout?.rev ?: 0
        return if (rev > appliedLayoutRev) rev else appliedLayoutRev
    }
}
