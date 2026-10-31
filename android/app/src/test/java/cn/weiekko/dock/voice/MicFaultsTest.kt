package cn.weiekko.dock.voice

import org.junit.Assert.assertEquals
import org.junit.Test

class MicFaultsTest {
    private fun faults(maxWaitMs: Long = 1_500L) = MicFaults(maxWaitMs = maxWaitMs)

    @Test
    fun firstErrorsBackOffInsteadOfSpinning() {
        val mic = faults()
        mic.opened()
        assertEquals(MicAction.Wait(60), mic.onError(10_000L))
        assertEquals(MicAction.Wait(120), mic.onError(10_100L))
    }

    @Test
    fun thirdErrorInARowReopensTheRecorder() {
        val mic = faults()
        mic.opened()
        mic.onError(10_000L)
        mic.onError(10_100L)
        assertEquals(MicAction.Reopen, mic.onError(10_200L))
        assertEquals(1, mic.reopens)
    }

    @Test
    fun oneGoodChunkResetsTheCount() {
        val mic = faults()
        mic.opened()
        mic.onError(10_000L)
        mic.onError(10_100L)
        mic.data()
        assertEquals(MicAction.Wait(60), mic.onError(10_200L))
    }

    @Test
    fun reopensDoNotStampedeWhenTheMicStaysTaken() {
        val mic = faults()
        mic.opened()
        mic.onError(10_000L)
        mic.onError(10_100L)
        assertEquals(MicAction.Reopen, mic.onError(10_200L))
        // 刚开完又坏：先等到间隔够了再试，而且一次最多等 maxWaitMs
        mic.onError(10_300L)
        mic.onError(10_400L)
        assertEquals(MicAction.Wait(1_500), mic.onError(10_500L))
        assertEquals(1, mic.reopens)
    }

    @Test
    fun waitIsCappedSoTheLoopStillChecksRunning() {
        val mic = faults(maxWaitMs = 400L)
        mic.opened()
        mic.onError(10_000L)
        mic.onError(10_100L)
        assertEquals(MicAction.Reopen, mic.onError(10_200L))
        mic.onError(10_300L)
        mic.onError(10_400L)
        assertEquals(MicAction.Wait(400), mic.onError(10_500L))
    }

    @Test
    fun aRecorderOpenedLongAgoMayReopenAgain() {
        val mic = faults()
        mic.opened()
        mic.onError(10_000L)
        mic.onError(10_100L)
        assertEquals(MicAction.Reopen, mic.onError(10_200L))
        mic.opened()
        mic.onError(20_000L)
        mic.onError(20_100L)
        assertEquals(MicAction.Reopen, mic.onError(20_200L))
        assertEquals(2, mic.reopens)
    }
}
