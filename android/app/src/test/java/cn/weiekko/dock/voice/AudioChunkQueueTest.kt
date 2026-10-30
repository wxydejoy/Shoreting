package cn.weiekko.dock.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioChunkQueueTest {
    private fun chunk(value: Float) = FloatArray(4) { value }

    @Test
    fun keepsArrivalOrder() {
        val queue = AudioChunkQueue(capacity = 4)
        val first = chunk(1f)
        queue.offer(first)
        queue.offer(chunk(2f))
        assertSame(first, queue.poll(10))
        assertEquals(1, queue.size)
    }

    @Test
    fun dropsOldestWhenDecoderFallsBehind() {
        val queue = AudioChunkQueue(capacity = 2)
        queue.offer(chunk(1f))
        queue.offer(chunk(2f))
        queue.offer(chunk(3f))
        assertEquals(1, queue.dropped)
        assertEquals(2, queue.size)
        // 留下的应该是最近的两块：旧语音先丢
        assertEquals(2f, queue.poll(10)!![0])
        assertEquals(3f, queue.poll(10)!![0])
    }

    @Test
    fun pollTimesOutInsteadOfBlockingForever() {
        val queue = AudioChunkQueue(capacity = 2)
        val started = System.currentTimeMillis()
        assertNull(queue.poll(30))
        assertTrue(System.currentTimeMillis() - started >= 20)
    }

    @Test
    fun closeWakesTheDecoder() {
        val queue = AudioChunkQueue(capacity = 2)
        val started = System.currentTimeMillis()
        Thread { runCatching { Thread.sleep(20) }.also { queue.close() } }.start()
        assertNull(queue.poll(2_000))
        assertTrue(System.currentTimeMillis() - started < 1_000)
    }
}
