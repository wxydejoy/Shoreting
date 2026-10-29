package cn.weiekko.dock.background

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.util.concurrent.atomic.AtomicReference

class BackgroundServerTest {
    @get:Rule
    val folder = TemporaryFolder()

    @Test
    fun pageRequiresHubTokenThenIndexesUpload() {
        val library = BackgroundLibrary(folder.root)
        val current = AtomicReference<String?>(null)
        val server = BackgroundServer(
            library = library,
            gate = BackgroundGate { "hub-token" },
            pageHtml = BACKGROUND_PAGE,
            currentId = { current.get() },
            onCurrent = { current.set(it) },
            port = 0,
            bindHost = "127.0.0.1",
        )
        val client = OkHttpClient()
        server.use {
            val base = "http://127.0.0.1:${server.port}"
            val page = client.newCall(Request.Builder().url("$base/").build()).execute()
            assertEquals(200, page.code)
            assertTrue(page.body!!.string().contains("Hub Token"))

            val locked = client.newCall(Request.Builder().url("$base/api/backgrounds").build()).execute()
            assertEquals(401, locked.code)

            val denied = client.newCall(
                Request.Builder().url("$base/api/login")
                    .post("""{"token":"nope"}""".toRequestBody(JSON))
                    .build(),
            ).execute()
            assertEquals(401, denied.code)

            val login = client.newCall(
                Request.Builder().url("$base/api/login")
                    .post("""{"token":"hub-token"}""".toRequestBody(JSON))
                    .build(),
            ).execute()
            assertEquals(200, login.code)
            val cookie = login.header("Set-Cookie")!!.substringBefore(';')

            val upload = client.newCall(
                Request.Builder().url("$base/api/backgrounds")
                    .header("Cookie", cookie)
                    .post(
                        MultipartBody.Builder().setType(MultipartBody.FORM)
                            .addFormDataPart("realname", "张三")
                            .addFormDataPart("file", "shore.png", PNG.toRequestBody("image/png".toMediaType()))
                            .build(),
                    )
                    .build(),
            ).execute()
            val uploaded = upload.body!!.string()
            assertEquals(upload.code.toString() + uploaded, 201, upload.code)
            assertTrue(uploaded.contains(""""current":"shore.png""""))
            assertTrue(uploaded.contains(""""owner":"张三""""))
            assertTrue(library.file("shore.png")!!.readBytes().contentEquals(PNG))

            library.import("older.mp4", "mp4".toByteArray(), BackgroundLibrary.MAX_BYTES)
            val selected = client.newCall(
                Request.Builder().url("$base/api/backgrounds/older.mp4/select")
                    .header("Cookie", cookie)
                    .post(ByteArray(0).toRequestBody(null))
                    .build(),
            ).execute()
            assertEquals(200, selected.code)
            assertEquals("older.mp4", current.get())

            val cleared = client.newCall(
                Request.Builder().url("$base/api/backgrounds/clear")
                    .header("Cookie", cookie)
                    .post(ByteArray(0).toRequestBody(null))
                    .build(),
            ).execute()
            assertTrue(cleared.body!!.string().contains(""""current":null"""))
            assertEquals(null, current.get())
            assertTrue(library.file("older.mp4")!!.isFile)

            val removed = client.newCall(
                Request.Builder().url("$base/api/backgrounds/shore.png")
                    .header("Cookie", cookie)
                    .delete()
                    .build(),
            ).execute()
            assertEquals(200, removed.code)
            assertFalse(library.index().any { it.id == "shore.png" })
        }
    }

    @Test
    fun uploadRejectsMissingNameAndFakeVideo() {
        val server = BackgroundServer(
            library = BackgroundLibrary(folder.root),
            gate = BackgroundGate { "hub-token" },
            pageHtml = BACKGROUND_PAGE,
            currentId = { null },
            onCurrent = {},
            port = 0,
            bindHost = "127.0.0.1",
        )
        val client = OkHttpClient()
        server.use {
            val base = "http://127.0.0.1:${server.port}"
            val login = client.newCall(
                Request.Builder().url("$base/api/login")
                    .post("""{"token":"hub-token"}""".toRequestBody(JSON))
                    .build(),
            ).execute()
            val cookie = login.header("Set-Cookie")!!.substringBefore(';')
            val nameless = client.newCall(
                Request.Builder().url("$base/api/backgrounds")
                    .header("Cookie", cookie)
                    .post(
                        MultipartBody.Builder().setType(MultipartBody.FORM)
                            .addFormDataPart("file", "shore.png", PNG.toRequestBody("image/png".toMediaType()))
                            .build(),
                    )
                    .build(),
            ).execute()
            assertEquals(400, nameless.code)
            assertTrue(nameless.body!!.string().contains("实名"))
            val fake = client.newCall(
                Request.Builder().url("$base/api/backgrounds")
                    .header("Cookie", cookie)
                    .post(
                        MultipartBody.Builder().setType(MultipartBody.FORM)
                            .addFormDataPart("realname", "张三")
                            .addFormDataPart("file", "clip.mp4", "not-a-video".toRequestBody("video/mp4".toMediaType()))
                            .build(),
                    )
                    .build(),
            ).execute()
            assertEquals(400, fake.code)
            assertTrue(fake.body!!.string().contains("不是有效的 MP4"))
        }
    }

    private companion object {
        val JSON = "application/json".toMediaType()
        val PNG = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)
    }
}
