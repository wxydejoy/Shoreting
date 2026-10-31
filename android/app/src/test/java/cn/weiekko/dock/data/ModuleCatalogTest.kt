package cn.weiekko.dock.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 模块目录（O5）：槽位 id 就是布局的存储键——老布局（"Clock"、"Win0"…）必须原样读得回来，
 * 默认布局必须给每个槽位一个矩形，编辑过的槽位和默认合并时以用户为准。
 */
class ModuleCatalogTest {

    @Test
    fun catalogCoversLegacySlotIds() {
        // 老枚举名一个都不能少，否则旧设备存的布局键读不出来
        val expected = setOf("Clock", "Pc", "Win0", "Win1", "Win2", "Device0", "Device1", "Media", "Weather", "Indoor")
        assertEquals(expected, ModuleCatalog.all.map { it.id }.toSet())
    }

    @Test
    fun multiInstanceSlotsCarryIndexes() {
        val win = ModuleCatalog.ofType(ModuleType.Win)
        assertEquals(listOf(0, 1, 2), win.map { it.index })
        val device = ModuleCatalog.ofType(ModuleType.Device)
        assertEquals(listOf(0, 1), device.map { it.index })
        // 单例种类没有序号
        ModuleCatalog.ofType(ModuleType.Clock).forEach { assertEquals(-1, it.index) }
        assertTrue(ModuleType.Win.multiInstance)
        assertFalse(ModuleType.Media.multiInstance)
    }

    @Test
    fun slotLookupIsCaseSensitiveById() {
        assertNotNull(ModuleCatalog.slot("Clock"))
        assertNull(ModuleCatalog.slot("clock"))
        assertNull(ModuleCatalog.slot("Nope"))
    }

    @Test
    fun defaultLayoutRectsEverySlot() {
        val layout = defaultDockLayout(canvasW = 800f, canvasH = 400f, tileW = 130f, tileH = 80f)
        ModuleCatalog.all.forEach { slot ->
            val rect = layout.rect(slot.id)
            assertNotNull("默认布局缺 ${slot.id} 的位置", rect)
            // 0..1 归一，尺寸不小于最小磁贴
            assertTrue(rect!!.w >= 0.08f && rect.w <= 1f)
            assertTrue(rect.h >= 0.08f && rect.h <= 1f)
            assertTrue(rect.x in 0f..1f && rect.y in 0f..1f)
        }
        // 存储键就是槽位 id 本身，键集合完全一致
        assertEquals(ModuleCatalog.all.map { it.id }.toSet(), layout.modules.keys)
    }

    @Test
    fun withRoundTripsAndZeroesPlaceholder() {
        val layout = defaultDockLayout(800f, 400f, 130f, 80f)
        val moved = layout.with("Win0", ModuleRect(x = 0.2f, y = 0.3f, w = 0.2f, h = 0.2f))
        assertEquals(0.2f, moved.rect("Win0")!!.x, 1e-4f)

        // 占位（几乎零尺寸）存成全零：不可见但保留 visible/chrome 的意图
        val hidden = layout.with("Media", ModuleRect(x = 0.1f, y = 0.1f, w = 0.01f, h = 0.01f, visible = false, chrome = false))
        val stored = hidden.rect("Media")!!
        assertEquals(0f, stored.w, 1e-6f)
        assertFalse(stored.visible)
        assertFalse(stored.chrome)
    }

    @Test
    fun mergedWithPrefersUserPlacementButKeepsDefaultForPlaceholder() {
        val defaults = defaultDockLayout(800f, 400f, 130f, 80f)
        // 用户布局是稀疏的：只存动过的键。动过位置的以用户为准；只隐藏过的（占位）位置回默认
        val user = DockLayout(
            modules = mapOf(
                "Clock" to ModuleRect(x = 0.1f, y = 0.1f, w = 0.5f, h = 0.5f),
                "Media" to ModuleRect(x = 0f, y = 0f, w = 0f, h = 0f, visible = false),
            ),
        )

        val merged = user.mergedWith(defaults)
        assertEquals(0.1f, merged.rect("Clock")!!.x, 1e-4f)
        val media = merged.rect("Media")!!
        assertFalse(media.visible)
        assertEquals(defaults.rect("Media")!!.x, media.x, 1e-4f)
    }

    @Test
    fun legacySerializedLayoutStillMerges() {
        // 老版本存下来的 JSON 就是这种键：反序列化后必须能和默认布局合并
        val legacy = DockLayout(
            version = 1,
            modules = mapOf(
                "Win0" to ModuleRect(x = 0.02f, y = 0.4f, w = 0.2f, h = 0.2f),
                "Device1" to ModuleRect(x = 0f, y = 0f, w = 0f, h = 0f, visible = false),
            ),
        )
        val defaults = defaultDockLayout(800f, 400f, 130f, 80f)
        val merged = legacy.mergedWith(defaults)
        assertEquals(0.02f, merged.rect("Win0")!!.x, 1e-4f)
        assertFalse(merged.rect("Device1")!!.visible)
        assertNotNull(merged.rect("Clock"))
    }
}
