package cn.weiekko.dock.voice

import android.content.Context
import android.util.Log
import java.io.File

/**
 * sherpa 的识别器只有两种加载模式，不能混：`assetManager` 非 null 时所有路径都按
 * asset 名去开，为 null 时才按文件路径开。
 *
 * 热词表是运行时写的（设备名来自 snapshot 的 `devices[]`），只能给绝对路径；模型却在
 * assets 里。两者塞给同一个 recognizer 时，sherpa 会把热词的绝对路径当 asset 名去找，
 * 打不开就在 native 层 abort——整个进程跟着退出，`runCatching` 那种降级根本捞不住
 * （真机日志：`Read binary file: Load '.../cache/asr-hotwords.txt' failed` 后进程死亡）。
 *
 * 所以模型先落到 filesDir，识别器一律走文件模式。KWS 仍在 assets 上，它没有热词。
 */
object AsrAssets {
    private const val TAG = "AsrAssets"

    /** 资产结构或解包逻辑变了就加一，旧目录会被重新覆盖。 */
    private const val VERSION = 1

    private val FILES = listOf(
        AsrModels.ENCODER,
        AsrModels.DECODER,
        AsrModels.JOINER,
        AsrModels.TOKENS,
    )

    /**
     * 确保模型已在 filesDir，返回目录；失败返回 null（调用方降级为只唤醒）。
     * 首次约 155MB，只做一次，之后的启动只 stat 一个标记文件。
     */
    fun ensure(context: Context): File? = runCatching {
        val root = File(context.filesDir, "asr")
        val mark = File(root, "extracted.v$VERSION")
        if (mark.isFile) return@runCatching root
        root.mkdirs()
        for (name in FILES) {
            val target = File(root, name.substringAfterLast('/'))
            // 先写 .part 再改名，解包中途被杀不会留下半个 onnx 冒充成品
            val tmp = File(root, "${target.name}.part")
            context.assets.open(name).use { input ->
                tmp.outputStream().use { output -> input.copyTo(output, 1 shl 20) }
            }
            if (!tmp.renameTo(target)) {
                tmp.copyTo(target, overwrite = true)
                tmp.delete()
            }
        }
        mark.writeText("v$VERSION")
        root
    }.onFailure { Log.e(TAG, "extract failed", it) }.getOrNull()

    /** 把 assets 里的相对名换成解包后的绝对路径。 */
    fun path(root: File, assetName: String): String =
        File(root, assetName.substringAfterLast('/')).absolutePath
}
