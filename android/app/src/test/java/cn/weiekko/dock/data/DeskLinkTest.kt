package cn.weiekko.dock.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DeskLinkTest {
    @Test
    fun hubOffKeepsLocalDeskWithoutDemoOrPolling() {
        val link = deskLink(hubEnabled = false, configured = true)
        assertEquals(DeskLink.Local, link)
        assertFalse(link.pollsHub)
        assertFalse(link.usesDemo)
        assertEquals("先在设置里打开 Hub", link.companionBlock)
    }

    @Test
    fun hubOnAndConfiguredPollsLiveData() {
        val link = deskLink(hubEnabled = true, configured = true)
        assertEquals(DeskLink.Live, link)
        assertTrue(link.pollsHub)
        assertFalse(link.usesDemo)
        assertEquals(null, link.companionBlock)
    }

    @Test
    fun hubOnButUnconfiguredStaysOnPreview() {
        val link = deskLink(hubEnabled = true, configured = false)
        assertEquals(DeskLink.Preview, link)
        assertFalse(link.pollsHub)
        assertTrue(link.usesDemo)
    }
}
