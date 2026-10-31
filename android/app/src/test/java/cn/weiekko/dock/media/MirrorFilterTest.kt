package cn.weiekko.dock.media

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MirrorFilterTest {
    @Test
    fun appListSplitsOnEverySeparator() {
        assertEquals(
            listOf("com.tencent.mm", "com.a.b"),
            MirrorFilter.parseAppList("com.tencent.mm，com.a.b\n com.a.b ;"),
        )
        assertEquals(emptyList<String>(), MirrorFilter.parseAppList("， ; \n"))
    }

    @Test
    fun disabledMeansNever() {
        assertFalse(
            MirrorFilter.shouldMirror(
                enabled = false,
                apps = setOf("com.tencent.mm"),
                packageName = "com.tencent.mm",
                ongoing = false,
                groupSummary = false,
                text = "饭好了",
            )
        )
    }

    @Test
    fun emptyWhitelistMeansNever() {
        assertFalse(
            MirrorFilter.shouldMirror(
                enabled = true,
                apps = emptySet(),
                packageName = "com.tencent.mm",
                ongoing = false,
                groupSummary = false,
                text = "饭好了",
            )
        )
    }

    @Test
    fun selfPackageNeverMirrors() {
        // 递话通知（F33）也是通知：回环进来就是无限复读
        assertFalse(
            MirrorFilter.shouldMirror(
                enabled = true,
                apps = setOf(MirrorFilter.SELF_PACKAGE),
                packageName = MirrorFilter.SELF_PACKAGE,
                ongoing = false,
                groupSummary = false,
                text = "hi",
            )
        )
    }

    @Test
    fun outsideWhitelistNeverMirrors() {
        assertFalse(
            MirrorFilter.shouldMirror(
                enabled = true,
                apps = setOf("com.tencent.mm"),
                packageName = "com.other.app",
                ongoing = false,
                groupSummary = false,
                text = "hi",
            )
        )
    }

    @Test
    fun ongoingAndGroupSummaryNeverMirror() {
        val base = {
            ongoing: Boolean, groupSummary: Boolean ->
            MirrorFilter.shouldMirror(
                enabled = true,
                apps = setOf("com.tencent.mm"),
                packageName = "com.tencent.mm",
                ongoing = ongoing,
                groupSummary = groupSummary,
                text = "hi",
            )
        }
        assertFalse(base(true, false))
        assertFalse(base(false, true))
    }

    @Test
    fun blankTextNeverMirrors() {
        val go = { text: CharSequence? ->
            MirrorFilter.shouldMirror(
                enabled = true,
                apps = setOf("com.tencent.mm"),
                packageName = "com.tencent.mm",
                ongoing = false,
                groupSummary = false,
                text = text,
            )
        }
        assertFalse(go(null))
        assertFalse(go("  "))
        assertTrue(go("饭好了"))
    }
}
