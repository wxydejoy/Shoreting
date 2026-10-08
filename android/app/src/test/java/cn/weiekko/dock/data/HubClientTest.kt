package cn.weiekko.dock.data

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class HubClientTest {
    private lateinit var server: MockWebServer
    private lateinit var client: HubClient
    private lateinit var connection: HubConnection

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = HubClient()
        connection = HubConnection(
            host = server.hostName,
            port = server.port,
            token = "secret",
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun healthAcceptsHelmHub() {
        server.enqueue(
            MockResponse().setBody(
                """{"ok":true,"service":"dock-hub","protocol":1,"name":"study"}""",
            ),
        )
        val health = client.health(connection)
        assertEquals("study", health.name)
        assertEquals(0, server.takeRequest().headers["Authorization"]?.length ?: 0)
    }

    @Test
    fun healthAcceptsMiniHubIp() {
        assertEquals(MiniConnection.DEFAULT_HOST, HubConnection.DEFAULT_HOST)
        server.enqueue(
            MockResponse().setBody(
                """{"ok":true,"service":"dock-hub","protocol":1,"name":"study"}""",
            ),
        )
        val health = client.health(connection)
        assertEquals("study", health.name)
        assertEquals(1, server.requestCount)
    }

    @Test
    fun healthRejectsMiniNamedHub() {
        server.enqueue(
            MockResponse().setBody(
                """{"ok":true,"service":"dock-hub","protocol":1,"name":"mini"}""",
            ),
        )
        try {
            client.health(connection)
            throw AssertionError("expected HubException")
        } catch (e: HubException) {
            assertEquals("bad_request", e.code)
            assertTrue(e.message!!.contains(HubConnection.DEFAULT_HOST))
        }
    }

    @Test
    fun snapshotParsesTemperatureAndDevices() {
        server.enqueue(
            MockResponse().setBody(
                """
                {
                  "protocol": 1,
                  "hub": {"name": "study", "mijia": "ok", "message": null},
                  "temperature": {
                    "id": "desk",
                    "name": "书桌",
                    "celsius": 26.4,
                    "humidity": 53,
                    "updated_at": "2026-08-15T15:30:01Z",
                    "online": true
                  },
                  "pc": {
                    "online": true,
                    "updated_at": "2026-08-15T15:30:01Z",
                    "cpu": {"percent": 24.1, "temp_celsius": 61.2},
                    "memory": {"percent": 47.5, "used_gb": 15.2, "total_gb": 32.0},
                    "gpu": {"name": "RTX 4070", "percent": 12.0, "temp_celsius": 52.0},
                    "fps": 144
                  },
                  "media": {
                    "online": true,
                    "playing": true,
                    "title": "Night Drive",
                    "artist": "Demo",
                    "app": "Music",
                    "updated_at": "2026-08-15T15:30:01Z"
                  },
                  "companion": {"ready": true, "voice": false, "speaking": false},
                  "devices": [
                    {"id":"lamp","name":"台灯","type":"light","online":true,"on":true,"brightness":60},
                    {"id":"plug","name":"显示器","type":"switch","online":true,"on":false},
                    {"id":"desk","name":"书桌","type":"sensor","online":true,"celsius":24.6,"humidity":48,
                     "props":[{"id":"celsius","name":"温度","number":24.6,"unit":"°C"}]}
                  ]
                }
                """.trimIndent(),
            ),
        )
        val snapshot = client.snapshot(connection)
        assertEquals(26.4, snapshot.temperature!!.celsius, 0.01)
        assertEquals(53.0, snapshot.temperature!!.humidity!!, 0.01)
        assertEquals(24.1, snapshot.pc!!.cpu!!.percent, 0.01)
        assertEquals(144.0, snapshot.pc!!.fps!!, 0.01)
        assertEquals("Night Drive", snapshot.media!!.title)
        assertTrue(snapshot.media!!.playing)
        assertTrue(snapshot.companion!!.ready)
        assertEquals(3, snapshot.devices.size)
        assertEquals(60, snapshot.devices[0].brightness)
        assertNull(snapshot.devices[1].brightness)
        assertEquals("sensor", snapshot.devices[2].type)
        val request = server.takeRequest()
        assertEquals("Bearer secret", request.getHeader("Authorization"))
        assertTrue(request.path!!.endsWith("/v1/snapshot"))
    }

    @Test
    fun commandSendsPartialBody() {
        server.enqueue(
            MockResponse().setBody(
                """{"id":"lamp","name":"台灯","type":"light","online":true,"on":true,"brightness":80}""",
            ),
        )
        val device = client.command(connection, "lamp", on = true, brightness = 80)
        assertEquals(80, device.brightness)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertTrue(request.path!!.endsWith("/v1/devices/lamp/command"))
        assertTrue(request.body.readUtf8().contains("\"brightness\":80"))
    }

    @Test
    fun mediaCommandSendsToggle() {
        server.enqueue(
            MockResponse().setBody(
                """{"online":true,"playing":false,"updated_at":"2026-08-15T15:30:01Z"}""",
            ),
        )
        val media = client.mediaCommand(connection, "toggle")
        assertEquals(false, media.playing)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertTrue(request.path!!.endsWith("/v1/devices/media/command"))
        assertTrue(request.body.readUtf8().contains("\"media\":\"toggle\""))
    }

    @Test
    fun companionChatSendsText() {
        server.enqueue(
            MockResponse().setBody(
                """{"text":"晚上好，漂泊者。","audio_id":null}""",
            ),
        )
        val reply = client.companionChat(connection, "晚上好。")
        assertEquals("晚上好，漂泊者。", reply.text)
        assertNull(reply.audioId)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertTrue(request.path!!.endsWith("/v1/companion/chat"))
        val raw = request.body.readUtf8()
        assertTrue(raw.contains("晚上好。"))
        assertTrue(!raw.contains("turn_id"))
    }

    @Test
    fun companionChatSendsTurnId() {
        server.enqueue(
            MockResponse().setBody(
                """{"text":"晚上好，漂泊者。","audio_id":"abc123def456"}""",
            ),
        )
        val reply = client.companionChat(connection, "晚上好。", "abc123def456")
        assertEquals("abc123def456", reply.audioId)
        val raw = server.takeRequest().body.readUtf8()
        assertTrue(raw.contains("\"turn_id\":\"abc123def456\""))
    }

    @Test
    fun companionStopPostsPath() {
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        client.companionStop(connection)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertTrue(request.path!!.endsWith("/v1/companion/stop"))
    }

    @Test
    fun companionAudioDownloadsWav() {
        val wav = byteArrayOf('R'.code.toByte(), 'I'.code.toByte(), 'F'.code.toByte(), 'F'.code.toByte())
        server.enqueue(
            MockResponse()
                .setHeader("Content-Type", "audio/wav")
                .setBody(okio.Buffer().write(wav)),
        )
        val bytes = client.companionAudio(connection, "turn-1")
        assertEquals(4, bytes.size)
        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertTrue(request.path!!.endsWith("/v1/companion/audio/turn-1"))
        assertEquals("Bearer secret", request.getHeader("Authorization"))
    }

    @Test
    fun companionAudioRejectsBadId() {
        try {
            client.companionAudio(connection, "../x")
            throw AssertionError("expected HubException")
        } catch (e: HubException) {
            assertEquals("bad_request", e.code)
        }
        assertEquals(0, server.requestCount)
    }

    @Test
    fun unauthorizedBecomesHubException() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(401)
                .setBody("""{"error":{"code":"unauthorized","message":"Token 不正确"}}"""),
        )
        try {
            client.snapshot(connection)
            throw AssertionError("expected HubException")
        } catch (e: HubException) {
            assertTrue(e.isUnauthorized)
            assertEquals("Token 不正确", e.message)
        }
    }

    @Test
    fun miniHealthAcceptsHelmMini() {
        server.enqueue(
            MockResponse().setBody(
                """{"ok":true,"service":"helm-mini","protocol":1,"name":"mini"}""",
            ),
        )
        val mini = MiniConnection(host = server.hostName, port = server.port, token = MiniConnection.DEFAULT_TOKEN)
        val health = client.miniHealth(mini)
        assertEquals("mini", health.name)
        assertEquals(0, server.takeRequest().headers["Authorization"]?.length ?: 0)
    }

    @Test
    fun miniSnapshotSendsBearerToken() {
        server.enqueue(
            MockResponse().setBody(
                """
                {
                  "protocol": 1,
                  "service": "helm-mini",
                  "pc": {
                    "online": true,
                    "updated_at": "2026-08-26T14:00:00Z",
                    "cpu": {"percent": 18.2},
                    "memory": {"percent": 41.0, "used_gb": 6.6, "total_gb": 16.0},
                    "gpu": {"name": "M4", "percent": 8.0}
                  }
                }
                """.trimIndent(),
            ),
        )
        val mini = MiniConnection(host = server.hostName, port = server.port)
        val pc = client.miniSnapshot(mini)
        assertEquals(18.2, pc.cpu!!.percent, 0.01)
        assertEquals(8.0, pc.gpu!!.percent!!, 0.01)
        assertEquals("M4", pc.gpu!!.name)
        val request = server.takeRequest()
        assertEquals("Bearer ${MiniConnection.DEFAULT_TOKEN}", request.getHeader("Authorization"))
        assertTrue(request.path!!.endsWith("/v1/snapshot"))
    }

    @Test
    fun companionTimeoutAllowsTethys() {
        assertTrue(HubClient.COMPANION_TIMEOUT_MS >= 90_000L)
    }
}
