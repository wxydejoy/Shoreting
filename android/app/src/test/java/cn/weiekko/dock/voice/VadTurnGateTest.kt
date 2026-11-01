package cn.weiekko.dock.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 断句时序的用例。VAD 的判断（是不是人声、有没有说完）是模型的事，
 * 这里只验证时序规则：段弹出 + 转写收尾后才允许 Tail。
 */
class VadTurnGateTest {
    @Test
    fun noSpeechStopsOnEmpty() {
        val gate = VadTurnGate()
        assertNull(gate.shouldStop(6_000L))
        assertEquals(VadTurnGate.StopReason.Empty, gate.shouldStop(6_600L))
    }

    @Test
    fun segmentAloneIsNotEnoughUntilTranscriptSettles() {
        val gate = VadTurnGate()
        gate.onSpeech(0L)
        gate.onText(400L)
        gate.onSegment(1_100L) // 人声 + 0.7s 尾静音，VAD 弹段
        // 段弹出 500ms 内不许断（转写收尾窗口）
        assertNull(gate.shouldStop(1_300L))
        assertEquals(VadTurnGate.StopReason.Tail, gate.shouldStop(1_700L))
    }

    @Test
    fun tailTextDuringSilencePostponesTheCut() {
        // 「关电脑」的形态：字音之间的静音凑出了段，
        // 但「电脑」两个字在静音尾巴里才落进转写——等转写停了再断。
        val gate = VadTurnGate()
        gate.onSpeech(0L)
        gate.onText(300L) // 「关」
        gate.onSegment(1_000L)
        gate.onText(1_200L) // 「电脑」这时才识别出来
        assertNull(gate.shouldStop(1_400L))
        assertNull(gate.shouldStop(1_650L))
        assertEquals(VadTurnGate.StopReason.Tail, gate.shouldStop(1_800L))
    }

    @Test
    fun vadSpeechCountsAsHeardEvenWithoutText() {
        // 有人声但识别不出字（比如纯噪音里的哼声）：段弹出就断，
        // 别干等 6.5s 的 Empty。
        val gate = VadTurnGate()
        gate.onSpeech(200L)
        gate.onSegment(900L)
        assertEquals(VadTurnGate.StopReason.Tail, gate.shouldStop(1_500L))
        assertTrue(gate.heard)
    }

    @Test
    fun maxStillCapsLongUtterances() {
        val gate = VadTurnGate()
        gate.onSpeech(0L)
        assertEquals(VadTurnGate.StopReason.Max, gate.shouldStop(16_000L))
    }

    @Test
    fun onlySegmentStillFiresTailAfterSettle() {
        // 防御性用例：segment 单独到达（理论上不该发生）也不能凭空提前断，
        // 仍要过 500ms 的转写收尾窗口。
        val gate = VadTurnGate()
        gate.onSegment(1_000L)
        assertNull(gate.shouldStop(1_200L))
        assertEquals(VadTurnGate.StopReason.Tail, gate.shouldStop(1_600L))
    }
}
