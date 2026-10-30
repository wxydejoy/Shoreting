package cn.weiekko.dock.data

/** 今天的皮肤（F36）：Hub 给日子，手机补天气，这里只挑一个。 */
object ThemeSkin {
    const val DEFAULT = "default"
    const val BIRTHDAY = "birthday"
    const val SPRING_FESTIVAL = "spring_festival"
    const val NEW_YEAR = "new_year"
    const val CHRISTMAS = "christmas"
    const val EXAM_WEEK = "exam_week"
    const val RAINY = "rainy"

    /** Hub 的 occasion 顺序就是优先级；雨垫底——过节那天下雨也先过节。 */
    fun pick(occasions: List<String>, rainy: Boolean): String {
        for (name in occasions) {
            when (name) {
                BIRTHDAY, SPRING_FESTIVAL, NEW_YEAR, CHRISTMAS, EXAM_WEEK -> return name
            }
        }
        return if (rainy) RAINY else DEFAULT
    }
}
