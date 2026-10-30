package cn.weiekko.dock.data

import org.junit.Assert.assertEquals
import org.junit.Test

class ThemeSkinTest {
    @Test
    fun hubOccasionsWinInOrder() {
        assertEquals(
            ThemeSkin.BIRTHDAY,
            ThemeSkin.pick(listOf("birthday", "new_year"), rainy = true),
        )
        assertEquals(ThemeSkin.SPRING_FESTIVAL, ThemeSkin.pick(listOf("spring_festival"), rainy = false))
        assertEquals(ThemeSkin.EXAM_WEEK, ThemeSkin.pick(listOf("exam_week"), rainy = false))
    }

    @Test
    fun rainyOnlyWhenNoOccasion() {
        assertEquals(ThemeSkin.RAINY, ThemeSkin.pick(emptyList(), rainy = true))
        assertEquals(ThemeSkin.RAINY, ThemeSkin.pick(listOf("nope"), rainy = true))
        assertEquals(ThemeSkin.DEFAULT, ThemeSkin.pick(emptyList(), rainy = false))
        // 过节那天下雨也先过节
        assertEquals(ThemeSkin.CHRISTMAS, ThemeSkin.pick(listOf("christmas"), rainy = true))
    }
}
