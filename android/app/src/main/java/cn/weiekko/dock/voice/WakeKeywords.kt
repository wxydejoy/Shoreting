package cn.weiekko.dock.voice

/** sherpa-onnx 中文 KWS 用声母+韵母（ppinyin），「小岸」无需再训练。 */
object WakeKeywords {
    const val PHRASE = "小岸"
    const val ALT = "嘿小岸"

    val LINES = listOf(
        "x iǎo àn :2.8 #0.15 @小岸",
        "h ēi x iǎo àn :3.0 #0.12 @嘿小岸",
    )

    /** createStream 用 `/` 分隔多行关键词。 */
    val STREAM: String = LINES.joinToString("/")

    const val ENCODER = "kws/encoder.int8.onnx"
    const val DECODER = "kws/decoder.onnx"
    const val JOINER = "kws/joiner.int8.onnx"
    const val TOKENS = "kws/tokens.txt"
    const val KEYWORDS_FILE = "kws/keywords.txt"
}
