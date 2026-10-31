package cn.weiekko.dock.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 控制台下发的手机配置怎么落地。
 *
 * 关键一条：**rev 没变大就什么都不做**。否则手机上刚手调过的字号会被下一轮
 * 3 秒轮询原样顶回去，而用户完全看不出是谁改的。
 */
class PhoneSyncTest {
    private fun remote(
        rev: Int = 1,
        tile: PhoneTile = PhoneTile(),
        type: PhoneType = PhoneType(),
        screen: PhoneScreen = PhoneScreen(),
        weather: PhoneWeather = PhoneWeather(),
    ) = PhoneSettings(rev = rev, tile = tile, type = type, screen = screen, weather = weather)

    @Test
    fun noRemoteMeansNoWork() {
        assertNull(PhoneSync.plan(null, 0))
    }

    @Test
    fun sameRevDoesNothing() {
        // 手机自己改过、rev 没动：这一轮必须放手
        assertNull(PhoneSync.plan(remote(rev = 3), appliedRev = 3))
    }

    @Test
    fun olderRevDoesNothing() {
        // Hub 回滚了配置（rev 变小）也不该把手机改回去
        assertNull(PhoneSync.plan(remote(rev = 2), appliedRev = 5))
        assertEquals(5, PhoneSync.nextRev(remote(rev = 2), appliedRev = 5))
    }

    @Test
    fun newerRevAppliesEverything() {
        val patch = PhoneSync.plan(
            remote(
                rev = 4,
                tile = PhoneTile(style = "frost", opacity = 70, corner = 26),
                type = PhoneType(font = "inter", clock = 120, stats = 24, tile = 18, chip = 14),
                screen = PhoneScreen(keepOn = false, sleepSec = 180),
                weather = PhoneWeather(enabled = false, city = " 杭州 "),
            ),
            appliedRev = 3,
        )
        requireNotNull(patch)
        assertEquals(TileStyle.Frost, patch.tile?.style)
        assertEquals(70, patch.tile?.opacityPercent)
        assertEquals(26, patch.tile?.cornerDp)
        assertEquals(DockFont.Inter, patch.type?.font)
        assertEquals(120, patch.type?.clockScalePercent)
        assertEquals(24, patch.type?.statsSize)
        assertEquals(18, patch.type?.tileSize)
        assertEquals(14, patch.type?.chipSize)
        assertEquals(false, patch.powerScreen)
        assertEquals(180, patch.sleepSec)
        assertEquals(false, patch.weatherEnabled)
        assertEquals("杭州", patch.weatherCity)
        assertEquals(4, PhoneSync.nextRev(remote(rev = 4), appliedRev = 3))
    }

    @Test
    fun outOfRangeIsClamped() {
        val patch = PhoneSync.plan(
            remote(
                tile = PhoneTile(opacity = 999, corner = 1),
                type = PhoneType(clock = 999, stats = 1, tile = 99, chip = 1),
                screen = PhoneScreen(sleepSec = 5),
            ),
            appliedRev = 0,
        )
        requireNotNull(patch)
        assertEquals(TileLook.OPACITY_MAX, patch.tile?.opacityPercent)
        assertEquals(TileLook.CORNER_MIN, patch.tile?.cornerDp)
        assertEquals(TypeLook.CLOCK_MAX, patch.type?.clockScalePercent)
        assertEquals(TypeLook.STATS_MIN, patch.type?.statsSize)
        assertEquals(TypeLook.TILE_MAX, patch.type?.tileSize)
        assertEquals(TypeLook.CHIP_MIN, patch.type?.chipSize)
        assertEquals(PhoneSync.MIN_SLEEP_SEC, patch.sleepSec)
    }

    @Test
    fun unknownEnumFallsBackInsteadOfCrashing() {
        val patch = PhoneSync.plan(
            remote(tile = PhoneTile(style = "glass"), type = PhoneType(font = "comic")),
            appliedRev = 0,
        )
        requireNotNull(patch)
        assertEquals(TileStyle.Ink, patch.tile?.style)
        assertEquals(DockFont.System, patch.type?.font)
    }

    @Test
    fun longCityIsTrimmed() {
        val long = "和".repeat(80)
        val patch = PhoneSync.plan(remote(weather = PhoneWeather(city = "  $long  ")), appliedRev = 0)
        requireNotNull(patch)
        assertEquals(PhoneSync.MAX_CITY, patch.weatherCity?.length)
    }

    @Test
    fun blankCityIsSkipped() {
        val patch = PhoneSync.plan(remote(weather = PhoneWeather(city = "   ")), appliedRev = 0)
        requireNotNull(patch)
        assertNull(patch.weatherCity)
    }

    @Test
    fun revOnlyMovesForward() {
        assertEquals(0, PhoneSync.nextRev(null, appliedRev = 0))
        assertEquals(7, PhoneSync.nextRev(remote(rev = 7), appliedRev = 6))
        assertEquals(7, PhoneSync.nextRev(remote(rev = 7), appliedRev = 7))
        assertEquals(9, PhoneSync.nextRev(remote(rev = 7), appliedRev = 9))
    }

    // ---------------------------------------------------------- 布局（独立 rev）

    private fun layoutRemote(
        rev: Int = 2,
        modules: Map<String, PhoneLayoutRect>,
    ) = remote(rev = 1).copy(layout = PhoneLayout(rev = rev, modules = modules))

    @Test
    fun noLayoutSectionMeansNoWork() {
        // 外观下发但没带布局（Hub 端 rev=0 时整段省略）
        assertNull(PhoneSync.layoutPlan(remote(rev = 1), appliedLayoutRev = 0))
    }

    @Test
    fun layoutSameRevDoesNothing() {
        val remote = layoutRemote(rev = 2, modules = mapOf("Clock" to PhoneLayoutRect(0.1f, 0.2f, 0.5f, 0.4f)))
        assertNull(PhoneSync.layoutPlan(remote, appliedLayoutRev = 2))
    }

    @Test
    fun newerLayoutApplies() {
        val remote = layoutRemote(
            rev = 3,
            modules = mapOf(
                "Clock" to PhoneLayoutRect(0.1f, 0.2f, 0.5f, 0.4f),
                "Media" to PhoneLayoutRect(visible = false),
                "Bogus" to PhoneLayoutRect(x = 5f, y = -3f, w = 9f, h = 9f),
            ),
        )
        val layout = requireNotNull(PhoneSync.layoutPlan(remote, appliedLayoutRev = 2))
        val clock = requireNotNull(layout.modules["Clock"])
        assertEquals(0.1f, clock.x, 1e-6f)
        assertEquals(0.4f, clock.h, 1e-6f)
        // 占位矩形（w/h 归零）：位置清零、显隐保留
        val media = requireNotNull(layout.modules["Media"])
        assertEquals(0f, media.w, 1e-6f)
        assertEquals(false, media.visible)
        // 越界值夹到 0..1
        val bogus = requireNotNull(layout.modules["Bogus"])
        assertEquals(1f, bogus.x, 1e-6f)
        assertEquals(0f, bogus.y, 1e-6f)
        assertEquals(1f, bogus.w, 1e-6f)
        assertEquals(3, PhoneSync.nextLayoutRev(remote, appliedLayoutRev = 2))
    }

    @Test
    fun layoutRevIsIndependentFromLookRev() {
        // 只保存布局：外观 rev 不动，字号不该被顶回去
        val remote = layoutRemote(rev = 1, modules = emptyMap())
        assertNull(PhoneSync.plan(remote, appliedRev = 4))
        assertNotNull(PhoneSync.layoutPlan(remote, appliedLayoutRev = 0))
        // 反过来：只保存外观时布局也不动
        val lookOnly = remote(rev = 5)
        assertNull(PhoneSync.layoutPlan(lookOnly, appliedLayoutRev = 3))
        assertNotNull(PhoneSync.plan(lookOnly, appliedRev = 4))
    }
}
