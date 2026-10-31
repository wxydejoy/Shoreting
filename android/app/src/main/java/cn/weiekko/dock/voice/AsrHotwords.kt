package cn.weiekko.dock.voice

/**
 * 流式 transducer 热词。汉字按字空格（cjkchar），**每行一个词**。
 * 用 `/` 拼会把整行当成一个词，sherpa 找不到 `/` 这个 token，全部编码失败（真机日志
 * `Cannot find ID for token /`，随后 `Failed to encode some hotwords`）。
 * 词表里没有的字整词丢掉，否则 sherpa 会整表热词失败。
 * 主屏设备名由 [setExtra] 在 snapshot 后追加，下次开口生效。
 */
object AsrHotwords {
    val BASE = listOf(
        "小岸",
        "嘿小岸",
        "无畏契约",
        "打瓦",
        "台灯",
        "开灯",
        "关灯",
        "打开",
        "关掉",
        "启动",
        "空调",
        "加湿器",
        "抖音",
        "Cursor",
    )

    /**
     * 中英混说（F6）：口语里常夹在中文中间的英文词和品牌名。
     * 识别器词表里没有的字整词会被 [forStream] 过滤掉，所以这里放心列——
     * 模型认不下的词不会把整张热词表搞挂，只是那一条不起作用。
     */
    val MIXED = listOf(
        "GitHub",
        "Git",
        "Python",
        "Java",
        "ChatGPT",
        "Steam",
        "WeGame",
        "Bilibili",
        "WiFi",
        "VPN",
        "APP",
        "PPT",
        "PDF",
        "Excel",
        "bug",
        "demo",
        "email",
        "link",
        "copy",
    )

    @Volatile
    var extra: List<String> = emptyList()
        private set

    /**
     * 词表改一次加一。识别器把热词表在 init 时读进 native，改不了；
     * 语音服务看着这个版本号决定要不要在两句之间重装一份识别器。
     */
    @Volatile
    var version: Int = 0
        private set

    fun setExtra(names: Iterable<String>) {
        val next = names.map { it.trim() }.filter { it.isNotEmpty() }.distinct()
        if (next == extra) return
        extra = next
        version += 1
    }

    fun parseTokens(tokensTxt: String): Set<String> {
        return tokensTxt.lineSequence()
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .map { it.split(Regex("\\s+")).first() }
            .toSet()
    }

    fun forStream(knownTokens: Set<String>? = null): String {
        return (BASE + MIXED + extra)
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .distinct()
            .map { toTokens(it) }
            .filter { encoded ->
                knownTokens == null || encoded.split(" ").all { it in knownTokens }
            }
            .joinToString("\n")
    }

    fun toTokens(phrase: String): String {
        val parts = mutableListOf<String>()
        val latin = StringBuilder()
        fun flushLatin() {
            if (latin.isNotEmpty()) {
                parts.add(latin.toString())
                latin.clear()
            }
        }
        for (ch in phrase.trim()) {
            when {
                ch.isWhitespace() -> flushLatin()
                ch in '\u4e00'..'\u9fff' -> {
                    flushLatin()
                    parts.add(ch.toString())
                }
                else -> latin.append(ch)
            }
        }
        flushLatin()
        return parts.joinToString(" ")
    }
}
