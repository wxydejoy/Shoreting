package cn.weiekko.dock.background

import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayOutputStream
import java.nio.charset.StandardCharsets

class BackgroundIntakeTest {
    @get:Rule
    val folder = TemporaryFolder()

    @Test
    fun realNameKeepsAPersonName() {
        assertEquals("张三", BackgroundIntake.realName(" 张 三 "))
        assertRejected("实名需为 2 到 20 个字") { BackgroundIntake.realName("李") }
        assertRejected("实名不能包含数字") { BackgroundIntake.realName("张三1") }
    }

    @Test
    fun imageMustMatchItsFormat() {
        val png = folder.newFile("a.png")
        png.writeBytes(PNG)
        BackgroundIntake.inspect(png, "a.png")

        val fake = folder.newFile("b.png")
        fake.writeBytes("png-bytes".toByteArray())
        assertRejected("不是有效的 PNG") { BackgroundIntake.inspect(fake, "b.png") }
        assertRejected("只接受 jpg、png、webp 或 mp4") { BackgroundIntake.inspect(png, "a.gif") }
    }

    @Test
    fun videoMustBePlayableMp4WithinThreeMinutes() {
        val good = folder.newFile("loop.mp4")
        good.writeBytes(mp4(timescale = 1000, duration = 5_000))
        BackgroundIntake.inspect(good, "loop.mp4")

        val text = folder.newFile("note.mp4")
        text.writeBytes("not a video".toByteArray())
        assertRejected("不是有效的 MP4") { BackgroundIntake.inspect(text, "note.mp4") }

        val clip = folder.newFile("short.mp4")
        clip.writeBytes(mp4(timescale = 1000, duration = 200))
        assertRejected("视频时长需在 1 秒到 3 分钟之间") { BackgroundIntake.inspect(clip, "short.mp4") }

        val silent = folder.newFile("silent.mp4")
        silent.writeBytes(mp4(timescale = 1000, duration = 5_000, video = false))
        assertRejected("视频里没有画面") { BackgroundIntake.inspect(silent, "silent.mp4") }
    }

    private fun assertRejected(message: String, block: () -> Unit) {
        try {
            block()
        } catch (error: BackgroundRejected) {
            assertEquals(message, error.message)
            return
        }
        throw AssertionError("expected rejection: $message")
    }

    private fun mp4(timescale: Long, duration: Long, video: Boolean = true): ByteArray {
        val ftyp = box("ftyp", "isom".toByteArray(ASCII) + i32(0) + "isom".toByteArray(ASCII))
        val mvhd = box("mvhd", ByteArray(12) + i32(timescale) + i32(duration) + ByteArray(80))
        val children = if (video) mvhd + hdlr("vide") else mvhd + hdlr("soun")
        return ftyp + box("moov", children)
    }

    private fun hdlr(kind: String): ByteArray {
        return box("hdlr", ByteArray(8) + kind.toByteArray(ASCII) + ByteArray(13))
    }

    private fun box(type: String, payload: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        out.write(i32((8 + payload.size).toLong()))
        out.write(type.toByteArray(ASCII))
        out.write(payload)
        return out.toByteArray()
    }

    private fun i32(value: Long): ByteArray {
        return byteArrayOf(
            (value shr 24).toByte(),
            (value shr 16).toByte(),
            (value shr 8).toByte(),
            value.toByte(),
        )
    }

    private companion object {
        val ASCII = StandardCharsets.US_ASCII
        val PNG = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)
    }
}
