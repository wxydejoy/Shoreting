package cn.weiekko.dock.data

enum class DockFont {
    System,
    Sans,
    Serif,
    Mono,
    Inter,
    Outfit,
    JetBrainsMono,
    Smiley,
    ;

    val id: String
        get() = when (this) {
            System -> "system"
            Sans -> "sans"
            Serif -> "serif"
            Mono -> "mono"
            Inter -> "inter"
            Outfit -> "outfit"
            JetBrainsMono -> "jetbrains"
            Smiley -> "smiley"
        }

    val label: String
        get() = when (this) {
            System -> "系统默认"
            Sans -> "系统黑体"
            Serif -> "系统宋体"
            Mono -> "系统等宽"
            Inter -> "Inter"
            Outfit -> "Outfit"
            JetBrainsMono -> "JetBrains Mono"
            Smiley -> "得意黑"
        }

    companion object {
        fun fromId(id: String?): DockFont =
            entries.firstOrNull { it.id == id } ?: System
    }
}

data class TypeLook(
    val font: DockFont = DockFont.System,
    val clockScalePercent: Int = 88,
    val statsSize: Int = 20,
    val tileSize: Int = 15,
    val chipSize: Int = 15,
) {
    /**
     * 主屏只有一档「内容字」：日期和她说的话同一个字号，轻重靠颜色分，不靠大小分。
     * 之前日期跟着 chipSize 长、字幕写死 14sp，把 chipSize 调低就会出现
     * 「日子比聊天还小」——同一层的字不该有两种命。
     */
    val contentSize: Int
        get() = (chipSize + 2).coerceIn(CHIP_MIN + 2, CHIP_MAX + 4)

    /**
     * 辅助信息（通知镜像、访客角标、壁纸那句话）：不跟手，固定小一档。
     * 这几处原本散落着 14 / 12 / 11 三个写死的值，比正文还大，层级是反的。
     */
    val metaSize: Int get() = 12
    val badgeSize: Int get() = 11

    companion object {
        const val CLOCK_MIN = 60
        const val CLOCK_MAX = 140
        const val STATS_MIN = 11
        const val STATS_MAX = 32
        const val TILE_MIN = 11
        const val TILE_MAX = 22
        const val CHIP_MIN = 11
        const val CHIP_MAX = 22
    }
}
