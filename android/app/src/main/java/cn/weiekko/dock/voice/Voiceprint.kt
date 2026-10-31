package cn.weiekko.dock.voice

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import kotlin.math.sqrt

/**
 * F5 声纹的纯逻辑半场：花名册、匹配、注册、一句话音频的攒取。
 * 音频只在 WakeWordService 里过一遍，进了这里就只剩向量——单测不需要任何安卓框架。
 */

/** 认出的人：称呼 + 声纹向量（注册时已归一化）。 */
@Serializable
data class VoiceprintProfile(val name: String, val embedding: List<Float>)

@Serializable
private data class VoiceprintData(val speakers: List<VoiceprintProfile> = emptyList())

/** 余弦相似度：两段向量越像越接近 1。调用方保证长度一致（同一只模型出的）。 */
fun cosineSimilarity(a: FloatArray, b: FloatArray): Float {
    if (a.isEmpty() || a.size != b.size) return 0f
    var dot = 0f
    var na = 0f
    var nb = 0f
    for (i in a.indices) {
        dot += a[i] * b[i]
        na += a[i] * a[i]
        nb += b[i] * b[i]
    }
    if (na == 0f || nb == 0f) return 0f
    return dot / (sqrt(na) * sqrt(nb))
}

/** 归一化到单位长度：注册时多句求平均才不会偏心嗓门大的那句。 */
fun normalize(vec: FloatArray): FloatArray {
    var sum = 0f
    for (v in vec) sum += v * v
    val norm = sqrt(sum)
    if (norm == 0f) return FloatArray(vec.size)
    return FloatArray(vec.size) { vec[it] / norm }
}

/**
 * 在家的几位：认人就是「跟谁的声纹最像，且像得过了线」。
 * 分数不过线就当没认出来——宁可少叫一声，别把客人叫成家里人。
 */
class VoiceprintRoster(val speakers: List<VoiceprintProfile> = emptyList()) {

    fun names(): List<String> = speakers.map { it.name }

    fun match(embedding: FloatArray, threshold: Float = MATCH_THRESHOLD): String? {
        var best: String? = null
        var bestScore = threshold
        for (speaker in speakers) {
            val score = cosineSimilarity(embedding, speaker.embedding.toFloatArray())
            if (score > bestScore) {
                bestScore = score
                best = speaker.name
            }
        }
        return best
    }

    /** 同名覆盖（重新注册就换新的），别的名字不动。 */
    fun enrolled(name: String, embedding: FloatArray): VoiceprintRoster {
        val kept = speakers.filter { it.name != name }
        return VoiceprintRoster(kept + VoiceprintProfile(name, normalize(embedding).toList()))
    }

    fun without(name: String): VoiceprintRoster =
        VoiceprintRoster(speakers.filter { it.name != name })
}

/** 花名册落盘（应用私有目录），服务认人、设置页增删都走这一个文件。 */
class VoiceprintStore(private val file: File) {

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    fun load(): VoiceprintRoster {
        if (!file.exists()) return VoiceprintRoster()
        // 文件坏了就当没注册过：声纹坏了不该影响她说话。
        return runCatching {
            val data = json.decodeFromString(VoiceprintData.serializer(), file.readText())
            VoiceprintRoster(data.speakers)
        }.getOrDefault(VoiceprintRoster())
    }

    fun save(roster: VoiceprintRoster) {
        file.parentFile?.mkdirs()
        file.writeText(json.encodeToString(VoiceprintData.serializer(), VoiceprintData(roster.speakers)))
    }
}

/** 注册期攒向量：凑够 N 句，取归一化均值当这位人的声纹。 */
class EnrollmentBuffer(private val need: Int = VoiceprintBus.ENROLL_UTTERANCES) {

    private val collected = ArrayList<FloatArray>()

    /** 收下第几句（从 1 数起）。 */
    fun offer(embedding: FloatArray): Int {
        if (collected.size < need && embedding.isNotEmpty()) {
            collected.add(normalize(embedding))
        }
        return collected.size
    }

    fun complete(): Boolean = collected.size >= need

    fun average(): FloatArray {
        if (collected.isEmpty()) return FloatArray(0)
        val dim = collected[0].size
        val sum = FloatArray(dim)
        for (c in collected) for (i in 0 until dim) sum[i] += c[i]
        return normalize(sum)
    }

    fun reset() = collected.clear()
}

/** 一句话的音频：从开口攒到闭嘴，最多留前 8 秒——认人够了，再多白烧电。 */
class UtteranceAudio(private val maxSamples: Int = EMBED_MAX_SAMPLES) {

    private val chunks = ArrayList<FloatArray>()
    private var total = 0

    fun add(chunk: FloatArray) {
        if (total >= maxSamples || chunk.isEmpty()) return
        val room = maxSamples - total
        if (chunk.size <= room) {
            chunks.add(chunk)
            total += chunk.size
        } else {
            chunks.add(chunk.copyOf(room))
            total = maxSamples
        }
    }

    /** 拼回一整段；没攒到东西就返回空。 */
    fun samples(): FloatArray {
        if (chunks.isEmpty()) return FloatArray(0)
        val out = FloatArray(total)
        var at = 0
        for (c in chunks) {
            c.copyInto(out, at)
            at += c.size
        }
        return out
    }

    fun clear() {
        chunks.clear()
        total = 0
    }

    val size: Int get() = total
}

/** 匹配过线标准：CAM++ 上 0.6 附近把「自己人」和「嗓音像的客人」分得开。 */
const val MATCH_THRESHOLD = 0.60f

/** 一句话最多喂给声纹模型的前 8 秒（16k 采样）。 */
const val EMBED_MAX_SAMPLES = 8 * 16_000

/** 少于 0.6 秒不喂：太短认不准，白白算一遍。 */
const val EMBED_MIN_SAMPLES = 9_600
