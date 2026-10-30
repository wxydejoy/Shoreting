package cn.weiekko.dock.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import kotlin.math.abs

class BurnGuardTest {
    @Test
    fun clockStaysPutForFirstFiveMinutes() {
        assertEquals(PixelShift(0, 0), BurnGuard.driftShift(0))
        assertEquals(PixelShift(0, 0), BurnGuard.driftShift(4))
    }

    @Test
    fun clockMovesEveryFiveMinutes() {
        assertEquals(PixelShift(2, 1), BurnGuard.driftShift(5))
        assertEquals(PixelShift(1, 2), BurnGuard.driftShift(10))
        assertEquals(PixelShift(-2, 1), BurnGuard.driftShift(20))
    }

    @Test
    fun clockCycleReturnsToOriginWithoutJump() {
        val period = BurnGuard.DRIFT_PATTERN.size.toLong() * BurnGuard.DRIFT_STEP_MINUTES
        assertEquals(PixelShift(0, 0), BurnGuard.driftShift(period))
        assertEquals(BurnGuard.driftShift(5), BurnGuard.driftShift(period + 5))
    }

    @Test
    fun driftPatternStepsStayWithinTwoPixels() {
        val pattern = BurnGuard.DRIFT_PATTERN
        for (i in pattern.indices) {
            val a = pattern[i]
            val b = pattern[(i + 1) % pattern.size]
            assertTrue("step $i too far: $a -> $b", abs(a.x - b.x) <= 2 && abs(a.y - b.y) <= 2)
        }
    }

    @Test
    fun negativeElapsedTreatedAsZero() {
        assertEquals(PixelShift(0, 0), BurnGuard.driftShift(-1))
        assertEquals(PixelShift(0, 0), BurnGuard.staticShift(-1))
    }

    @Test
    fun canvasDoesNotMoveBeforeThirtyMinutes() {
        assertEquals(PixelShift(0, 0), BurnGuard.staticShift(0))
        assertEquals(PixelShift(0, 0), BurnGuard.staticShift(29))
    }

    @Test
    fun canvasShiftsEveryThirtyMinutesAndCycles() {
        assertEquals(PixelShift(3, 2), BurnGuard.staticShift(30))
        assertEquals(PixelShift(2, -3), BurnGuard.staticShift(60))
        assertEquals(PixelShift(-3, 1), BurnGuard.staticShift(90))
        assertEquals(PixelShift(0, 0), BurnGuard.staticShift(120))
        assertEquals(BurnGuard.staticShift(30), BurnGuard.staticShift(150))
    }

    @Test
    fun nightDimCoversElevenPmToSevenAm() {
        assertEquals(BurnGuard.NIGHT_DIM_ALPHA, BurnGuard.nightDimFactor(LocalDateTime.of(2026, 10, 30, 23, 0)), 1e-6f)
        assertEquals(BurnGuard.NIGHT_DIM_ALPHA, BurnGuard.nightDimFactor(LocalDateTime.of(2026, 10, 30, 2, 30)), 1e-6f)
        assertEquals(BurnGuard.NIGHT_DIM_ALPHA, BurnGuard.nightDimFactor(LocalDateTime.of(2026, 10, 30, 6, 59)), 1e-6f)
        assertEquals(1f, BurnGuard.nightDimFactor(LocalDateTime.of(2026, 10, 30, 7, 0)), 1e-6f)
        assertEquals(1f, BurnGuard.nightDimFactor(LocalDateTime.of(2026, 10, 30, 12, 0)), 1e-6f)
        assertEquals(1f, BurnGuard.nightDimFactor(LocalDateTime.of(2026, 10, 30, 22, 59)), 1e-6f)
    }

    @Test
    fun nightDimCrossesMidnight() {
        assertEquals(BurnGuard.NIGHT_DIM_ALPHA, BurnGuard.nightDimFactor(LocalDateTime.of(2026, 10, 31, 0, 5)), 1e-6f)
    }
}
