package cn.weiekko.dock.background

import java.io.File
import java.io.RandomAccessFile

object BackgroundIntake {
    const val IMAGE_MAX_BYTES = 20L * 1024 * 1024
    const val VIDEO_MAX_BYTES = 200L * 1024 * 1024
    const val MIN_VIDEO_MS = 1_000L
    const val MAX_VIDEO_MS = 180_000L

    fun realName(raw: String): String {
        val name = raw.trim().replace(Regex("\\s+"), "")
        val letters = name.codePoints().filter { Character.isLetter(it) }.count()
        if (letters < 2 || name.length > 20) throw BackgroundRejected("实名需为 2 到 20 个字")
        if (name.any { it.isDigit() }) throw BackgroundRejected("实名不能包含数字")
        return name
    }

    fun inspect(file: File, filename: String) {
        when (extension(filename)) {
            "jpg", "jpeg" -> image(file, ::isJpeg, "不是有效的 JPEG", "图片不能超过 20MB")
            "png" -> image(file, ::isPng, "不是有效的 PNG", "图片不能超过 20MB")
            "webp" -> image(file, ::isWebp, "不是有效的 WebP", "图片不能超过 20MB")
            "mp4" -> {
                if (file.length() > VIDEO_MAX_BYTES) throw BackgroundRejected("视频不能超过 200MB")
                verifyMp4(file)
            }
            else -> throw BackgroundRejected("只接受 jpg、png、webp 或 mp4")
        }
    }

    private fun image(file: File, magic: (File) -> Boolean, invalid: String, tooBig: String) {
        if (file.length() > IMAGE_MAX_BYTES) throw BackgroundRejected(tooBig)
        if (!magic(file)) throw BackgroundRejected(invalid)
    }

    private fun verifyMp4(file: File) {
        RandomAccessFile(file, "r").use { source ->
            val found = Mp4Scan(source, source.length())
            found.walk(0, source.length())
            if (!found.ftyp) throw BackgroundRejected("不是有效的 MP4")
            if (!found.video) throw BackgroundRejected("视频里没有画面")
            val duration = found.durationMs ?: throw BackgroundRejected("视频无法核验")
            if (duration < MIN_VIDEO_MS || duration > MAX_VIDEO_MS) {
                throw BackgroundRejected("视频时长需在 1 秒到 3 分钟之间")
            }
        }
    }

    private fun extension(filename: String): String {
        val base = filename.substringAfterLast('/').substringAfterLast('\\')
        val dot = base.lastIndexOf('.')
        if (dot <= 0) return ""
        return base.substring(dot + 1).lowercase()
    }

    private fun isJpeg(file: File): Boolean {
        val head = head(file, 3)
        return head.size >= 3 && head[0] == 0xFF.toByte() && head[1] == 0xD8.toByte() && head[2] == 0xFF.toByte()
    }

    private fun isPng(file: File): Boolean {
        val head = head(file, 8)
        val sig = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)
        return head.size == 8 && head.contentEquals(sig)
    }

    private fun isWebp(file: File): Boolean {
        val head = head(file, 12)
        if (head.size < 12) return false
        return String(head, 0, 4, Charsets.US_ASCII) == "RIFF" &&
            String(head, 8, 4, Charsets.US_ASCII) == "WEBP"
    }

    private fun head(file: File, count: Int): ByteArray {
        file.inputStream().use { input ->
            val buffer = ByteArray(count)
            val read = input.read(buffer)
            return if (read <= 0) ByteArray(0) else buffer.copyOf(read)
        }
    }
}

private class Mp4Scan(private val source: RandomAccessFile, private val end: Long) {
    var ftyp: Boolean = false
    var video: Boolean = false
    var durationMs: Long? = null

    fun walk(start: Long, limit: Long) {
        var position = start
        while (position + 8 <= limit) {
            source.seek(position)
            val size = u32()
            val type = type()
            val header = if (size == 1L) 16 else 8
            val boxEnd = when (size) {
                0L -> limit
                1L -> {
                    val large = u64()
                    if (large < header) return
                    position + large
                }
                else -> position + size
            }
            if (boxEnd < position + header || boxEnd > end) return
            when (type) {
                "ftyp" -> ftyp = true
                "mvhd" -> durationMs = duration(position + header, boxEnd)
                "hdlr" -> if (handler(position + header, boxEnd) == "vide") video = true
                "moov", "trak", "mdia", "minf" -> walk(position + header, boxEnd)
            }
            if (boxEnd <= position) return
            position = boxEnd
        }
    }

    private fun duration(start: Long, boxEnd: Long): Long? {
        if (boxEnd - start < 20) return null
        source.seek(start)
        val version = source.read()
        source.seek(start + 1)
        if (version == 0) {
            if (boxEnd - start < 20) return null
            source.seek(start + 12)
            val timescale = u32()
            val duration = u32()
            if (timescale <= 0L) return null
            return duration * 1000 / timescale
        }
        if (boxEnd - start < 32) return null
        source.seek(start + 20)
        val timescale = u32()
        val duration = u64()
        if (timescale <= 0L) return null
        return duration * 1000 / timescale
    }

    private fun handler(start: Long, boxEnd: Long): String {
        if (boxEnd - start < 12) return ""
        source.seek(start + 8)
        return type()
    }

    private fun u32(): Long {
        val value = source.readInt()
        return value.toLong() and 0xffff_ffffL
    }

    private fun u64(): Long {
        val high = u32()
        val low = u32()
        return (high shl 32) or low
    }

    private fun type(): String {
        val bytes = ByteArray(4)
        source.readFully(bytes)
        return String(bytes, Charsets.US_ASCII)
    }
}
