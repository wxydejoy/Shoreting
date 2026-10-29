package cn.weiekko.dock.voice

object AsrModels {
    // assets/asr 里是 zipformer2 transducer（encoder/decoder/joiner），不是单文件 CTC 模型。
    // 之前指向不存在的 asr/model.int8.onnx，sherpa 原生层加载失败会直接 exit(255)，整个进程被杀 -> 黑屏。
    const val ENCODER = "asr/encoder.int8.onnx"
    const val DECODER = "asr/decoder.onnx"
    const val JOINER = "asr/joiner.int8.onnx"
    const val TOKENS = "asr/tokens.txt"

    fun display(raw: String): String {
        return raw.replace("▁", " ")
            .replace(Regex("\\s+"), " ")
            .trim()
    }
}
