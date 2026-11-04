package cn.weiekko.dock.voice

/**
 * sherpa-onnx 中文 KWS 用声母+韵母（ppinyin），「守岸人」无需再训练。
 *
 * 行格式：`<声母 韵母 ...> :<boost> #<threshold> @<显示名>`。
 * boost 越大越敏感，threshold 越小越容易触发；两项都偏敏感一侧，
 * 因为摆件离人一米以上、说话还隔着房间噪音。
 * 改这里必须同步改 assets/kws/keywords.txt，否则 createStream 与
 * keywordsFile 两条来源会不一致。
 */
object WakeKeywords {
    const val PHRASE = "守岸人"
    const val ALT = "嘿守岸人"

    /**
     * 主行之外再收几条常见误读的变体（守→收、岸→安/ǎn）：发音不标准也能唤醒。
     * 变体显示名都写「守岸人」，日志与界面上看不出差别。
     * 变体阈值略松（#0.06）——它们本来就只在发音偏掉时才有声学证据，
     * 正常发音仍由主行（#0.07）先命中，不会因此多出误唤醒。
     */
    val LINES = listOf(
        "sh ǒu àn r én :3.2 #0.07 @守岸人",
        "sh ōu àn r én :3.2 #0.06 @守岸人",
        "sh ǒu ān r én :3.2 #0.06 @守岸人",
        "sh ǒu ǎn r én :3.2 #0.06 @守岸人",
        "h ēi sh ǒu àn r én :3.4 #0.05 @嘿守岸人",
    )

    /** createStream 用 `/` 分隔多行关键词。 */
    val STREAM: String = LINES.joinToString("/")

    const val ENCODER = "kws/encoder.int8.onnx"
    const val DECODER = "kws/decoder.onnx"
    const val JOINER = "kws/joiner.int8.onnx"
    const val TOKENS = "kws/tokens.txt"
    const val KEYWORDS_FILE = "kws/keywords.txt"
}
