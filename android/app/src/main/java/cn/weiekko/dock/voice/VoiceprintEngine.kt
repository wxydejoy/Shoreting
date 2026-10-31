package cn.weiekko.dock.voice

import android.content.res.AssetManager
import android.util.Log
import com.k2fsa.sherpa.onnx.SpeakerEmbeddingExtractor
import com.k2fsa.sherpa.onnx.SpeakerEmbeddingExtractorConfig

/**
 * F5 声纹的原生半场：一段 16k 人声进，一个向量出（CAM++，192 维）。
 * 向量只在本机算，这里不做匹配也不存音频；模型没装好（fetch-voiceprint.sh
 * 没跑）就当这功能不存在，别让她为声纹卡住说话。
 */
class VoiceprintEngine(private val assets: AssetManager) {

    private var extractor: SpeakerEmbeddingExtractor? = null

    /** 模型装不起来就别每次都再试：一句 28MB 的 onnx 读进来要一阵。 */
    @Volatile
    var failed = false
        private set

    private fun ensure(): SpeakerEmbeddingExtractor? {
        extractor?.let { return it }
        if (failed) return null
        return runCatching {
            SpeakerEmbeddingExtractor(
                assets,
                SpeakerEmbeddingExtractorConfig(model = VoiceprintAssets.MODEL),
            )
        }.onFailure { Log.w(TAG, "voiceprint model unavailable: ${it.message}") }
            .onSuccess { extractor = it }
            .getOrNull() ?: run {
                failed = true
                null
            }
    }

    /** 人声向量；音频太短或模型不在就返回 null。 */
    fun embed(samples: FloatArray): FloatArray? {
        if (samples.size < EMBED_MIN_SAMPLES) return null
        val ext = ensure() ?: return null
        return runCatching {
            val stream = ext.createStream()
            try {
                stream.acceptWaveform(samples, SAMPLE_RATE)
                stream.inputFinished()
                if (!ext.isReady(stream)) null else ext.compute(stream)
            } finally {
                stream.release()
            }
        }.onFailure { Log.w(TAG, "voiceprint embed failed", it) }.getOrNull()
    }

    fun release() {
        extractor?.release()
        extractor = null
    }

    companion object {
        private const val TAG = "Voiceprint"
        private const val SAMPLE_RATE = 16_000
    }
}

/** 模型在 assets 里的位置，与 fetch-voiceprint.sh 对齐。 */
object VoiceprintAssets {
    const val MODEL = "voiceprint/model.onnx"
}
