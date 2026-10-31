package cn.weiekko.dock.voice

import java.util.ArrayDeque

/**
 * 采集线程 → 解码线程的音频队列。
 *
 * 以前读麦克风和解码在同一条线程里：`decode()` 一旦比 50ms 的块慢，AudioRecord
 * 的内置缓冲就溢出，溢出的恰好是「正在说的那几个字」，听起来就是说了没听到。
 * 现在采集线程只负责搬数据，解码慢一点只是队列变长。
 *
 * 队列满了丢最旧的一块（而不是等）：丢 50ms 的旧音频几乎听不出来，阻塞采集
 * 线程则会把内置缓冲撑爆。
 */
internal class AudioChunkQueue(private val capacity: Int = 40) {
    private val lock = Object()
    private val items = ArrayDeque<FloatArray>()
    private var closed = false

    /** 因为解码跟不上而丢掉的块数，用来判断是不是这台机器跑不动。 */
    var dropped = 0
        private set

    fun offer(chunk: FloatArray) {
        synchronized(lock) {
            if (closed) return
            while (items.size >= capacity) {
                items.pollFirst()
                dropped++
            }
            items.addLast(chunk)
            lock.notifyAll()
        }
    }

    /** 取一块；`timeoutMs` 内没有新数据就返回 null，方便上层检查是否该退出。 */
    fun poll(timeoutMs: Long): FloatArray? {
        val deadline = System.nanoTime() + timeoutMs * 1_000_000L
        synchronized(lock) {
            while (true) {
                items.pollFirst()?.let { return it }
                if (closed) return null
                val leftMs = (deadline - System.nanoTime()) / 1_000_000L
                if (leftMs <= 0L) return null
                lock.wait(leftMs)
            }
        }
    }

    fun close() {
        synchronized(lock) {
            closed = true
            lock.notifyAll()
        }
    }

    /** 丢掉还排着队的旧音频：麦克风断过档，之前那半句接不上了。 */
    fun clear() {
        synchronized(lock) {
            items.clear()
        }
    }

    val size: Int
        get() = synchronized(lock) { items.size }
}
