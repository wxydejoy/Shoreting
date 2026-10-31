package cn.weiekko.dock.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** F5 声纹纯逻辑：花名册、匹配、注册、一句话音频的攒取。 */
class VoiceprintTest {

    // 三个人占三个方向：妈妈偏 dim0，爸爸偏 dim1，陌生人偏 dim2。
    private fun mom(dir0: Float = 1f, dir1: Float = 0f) = normalize(floatArrayOf(dir0, dir1, 0f))
    private fun dad(dir0: Float = 0f, dir1: Float = 1f) = normalize(floatArrayOf(dir0, dir1, 0f))
    private fun stranger() = normalize(floatArrayOf(0f, 0f, 1f))

    @Test
    fun `cosine of parallel vectors is one`() {
        assertEquals(1f, cosineSimilarity(mom(), mom(dir0 = 3f)), 1e-5f)
    }

    @Test
    fun `cosine of mismatched or empty vectors is zero`() {
        assertEquals(0f, cosineSimilarity(FloatArray(0), FloatArray(0)))
        assertEquals(0f, cosineSimilarity(mom(), FloatArray(8)))
    }

    @Test
    fun `match returns the closest name above threshold`() {
        val roster = VoiceprintRoster(
            listOf(
                VoiceprintProfile("妈妈", mom().toList()),
                VoiceprintProfile("爸爸", dad().toList()),
            ),
        )
        assertEquals("妈妈", roster.match(mom()))
        assertEquals("爸爸", roster.match(dad()))
        // 稍微偏一点的口音也认得出：还是离妈妈最近
        assertEquals("妈妈", roster.match(mom(dir0 = 5f, dir1 = 1f)))
        assertNull(roster.match(stranger()))
    }

    @Test
    fun `match stays silent when nobody clears the threshold`() {
        val roster = VoiceprintRoster(listOf(VoiceprintProfile("妈妈", mom().toList())))
        assertNull(roster.match(stranger()))
        // 刚好压线不算认出：得严格高于阈值
        assertNull(roster.match(mom(), threshold = 1f))
    }

    @Test
    fun `enrolling replaces the same name and keeps others`() {
        var roster = VoiceprintRoster(listOf(VoiceprintProfile("妈妈", mom().toList())))
        roster = roster.enrolled("妈妈", mom(dir0 = 5f, dir1 = 1f))
        roster = roster.enrolled("爸爸", dad())
        assertEquals(listOf("妈妈", "爸爸"), roster.names())
        // 妈妈的声纹换成了新录的那份
        assertEquals("妈妈", roster.match(mom(dir0 = 5f, dir1 = 1f)))
        assertEquals(1f, cosineSimilarity(mom(dir0 = 5f, dir1 = 1f), roster.speakers[0].embedding.toFloatArray()), 1e-5f)
    }

    @Test
    fun `without removes only that name`() {
        val roster = VoiceprintRoster(
            listOf(
                VoiceprintProfile("妈妈", mom().toList()),
                VoiceprintProfile("爸爸", dad().toList()),
            ),
        )
        assertEquals(listOf("爸爸"), roster.without("妈妈").names())
        assertEquals(2, roster.without("客人").names().size)
    }

    @Test
    fun `store roundtrips roster through a file`() {
        val dir = File(System.getProperty("java.io.tmpdir"), "vp-${System.nanoTime()}")
        dir.mkdirs()
        try {
            val store = VoiceprintStore(File(dir, "voiceprint.json"))
            assertEquals(0, store.load().names().size)
            store.save(store.load().enrolled("妈妈", mom()).enrolled("爸爸", dad()))
            val loaded = VoiceprintStore(File(dir, "voiceprint.json")).load()
            assertEquals(listOf("妈妈", "爸爸"), loaded.names())
            assertEquals("妈妈", loaded.match(mom()))
            assertNull(loaded.match(stranger()))
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test
    fun `store with a broken file falls back to an empty roster`() {
        val dir = File(System.getProperty("java.io.tmpdir"), "vp-${System.nanoTime()}")
        dir.mkdirs()
        try {
            File(dir, "voiceprint.json").writeText("{not json")
            assertEquals(0, VoiceprintStore(File(dir, "voiceprint.json")).load().names().size)
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test
    fun `enrollment averages three utterances of the same person`() {
        val buffer = EnrollmentBuffer()
        assertEquals(1, buffer.offer(mom()))
        assertEquals(2, buffer.offer(mom(dir0 = 5f, dir1 = 1f)))
        assertEquals(3, buffer.offer(mom(dir0 = 4f, dir1 = 1f)))
        assertTrue(buffer.complete())
        val averaged = buffer.average()
        // 三句都在妈妈的方向上：均值还是妈妈，不是别人
        assertTrue(cosineSimilarity(averaged, mom()) > 0.9f)
        assertTrue(cosineSimilarity(averaged, stranger()) < 0.1f)
        // 多出来的第四句不再收
        assertEquals(3, buffer.offer(stranger()))
    }

    @Test
    fun `enrollment is incomplete before enough utterances`() {
        val buffer = EnrollmentBuffer()
        buffer.offer(mom())
        assertFalse(buffer.complete())
        buffer.reset()
        assertEquals(1, buffer.offer(mom()))
        assertFalse(buffer.complete())
    }

    @Test
    fun `utterance audio keeps only the first max seconds`() {
        val audio = UtteranceAudio(maxSamples = 1000)
        audio.add(FloatArray(600) { it / 600f })
        audio.add(FloatArray(600) { 1f - it / 600f })
        val samples = audio.samples()
        assertEquals(1000, samples.size)
        // 后到的块截到满为止，前面的不动
        assertEquals(0.9983f, samples[599], 1e-3f)
        audio.clear()
        assertEquals(0, audio.size)
    }
}
