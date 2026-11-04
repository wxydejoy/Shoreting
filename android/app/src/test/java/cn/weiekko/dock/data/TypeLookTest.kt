package cn.weiekko.dock.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TypeLookTest {
    /** 日期和她说的话是同一档内容字——「日子比聊天小」不该再出现。 */
    @Test
    fun contentSizeSitsAboveChip() {
        for (chip in TypeLook.CHIP_MIN..TypeLook.CHIP_MAX) {
            val look = TypeLook(chipSize = chip)
            assertTrue(look.contentSize > chip)
        }
        assertEquals(17, TypeLook().contentSize)
    }

    /** 辅助信息（镜像 / 访客角标 / 壁纸）必须比内容字小，否则层级反了。 */
    @Test
    fun metaStaysBelowContent() {
        for (chip in TypeLook.CHIP_MIN..TypeLook.CHIP_MAX) {
            val look = TypeLook(chipSize = chip)
            assertTrue(look.metaSize < look.contentSize)
            assertTrue(look.badgeSize <= look.metaSize)
        }
    }

    /** 极限字号下不许溢出成一整块巨字。 */
    @Test
    fun contentSizeIsClamped() {
        assertEquals(TypeLook.CHIP_MIN + 2, TypeLook(chipSize = TypeLook.CHIP_MIN).contentSize)
        assertEquals(TypeLook.CHIP_MAX + 2, TypeLook(chipSize = TypeLook.CHIP_MAX).contentSize)
    }
}
