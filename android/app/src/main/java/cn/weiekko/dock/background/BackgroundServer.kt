package cn.weiekko.dock.background

import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import java.nio.charset.StandardCharsets
import java.util.concurrent.Executors

class BackgroundServer(
    private val library: BackgroundLibrary,
    private val gate: BackgroundGate,
    private val pageHtml: String,
    private val currentId: () -> String?,
    private val onCurrent: (String?) -> Unit,
    port: Int,
    bindHost: String = "0.0.0.0",
) : AutoCloseable {
    private val socket = ServerSocket()
    private val workers = Executors.newCachedThreadPool()
    private val acceptThread: Thread
    val port: Int

    init {
        socket.reuseAddress = true
        socket.bind(InetSocketAddress(bindHost, port))
        this.port = socket.localPort
        acceptThread = Thread({
            while (!socket.isClosed) {
                val client = try {
                    socket.accept()
                } catch (_: Exception) {
                    break
                }
                workers.execute {
                    try {
                        handle(client)
                    } catch (_: Throwable) {
                        runCatching { client.close() }
                    }
                }
            }
        }, "background-page")
        acceptThread.isDaemon = true
        acceptThread.start()
    }

    override fun close() {
        socket.close()
        workers.shutdownNow()
        acceptThread.join(1000)
    }

    private fun handle(client: Socket) {
        client.use { connection ->
            connection.soTimeout = 60_000
            val input = connection.getInputStream()
            val output = connection.getOutputStream()
            try {
                val head = readHead(input) ?: return@use writeBytes(output, 400, "application/json; charset=utf-8", errorJson("请求不完整"))
                route(head, input, output)
        } catch (error: BackgroundRejected) {
            runCatching { writeBytes(output, 400, JSON, errorJson(error.message ?: "无法保存")) }
        } catch (_: Throwable) {
            runCatching { writeBytes(output, 500, JSON, errorJson("保存失败")) }
        }
        }
    }

    private fun route(head: Head, input: InputStream, output: OutputStream) {
        val parts = head.path.split('/')
        when {
            head.method == "GET" && head.path == "/" ->
                writeBytes(output, 200, "text/html; charset=utf-8", pageHtml.trim().toByteArray(UTF_8))
            head.method == "POST" && head.path == "/api/login" -> login(head, input, output)
            head.method == "POST" && head.path == "/api/logout" -> logout(head, output)
            head.method == "GET" && head.path == "/api/backgrounds" -> {
                if (!authorized(head, output)) return
                writeBytes(output, 200, JSON, indexJson().toByteArray(UTF_8))
            }
            head.method == "POST" && head.path == "/api/backgrounds" -> {
                if (!authorized(head, output)) return
                upload(head, input, output)
            }
            head.method == "POST" && head.path == "/api/backgrounds/clear" -> {
                if (!authorized(head, output)) return
                discardBody(head, input)
                onCurrent(null)
                writeBytes(output, 200, JSON, indexJson().toByteArray(UTF_8))
            }
            head.method == "POST" && parts.size == 5 && parts[1] == "api" && parts[2] == "backgrounds" && parts[4] == "select" -> {
                if (!authorized(head, output)) return
                discardBody(head, input)
                val id = decodePart(parts[3])
                if (library.file(id) == null) {
                    writeBytes(output, 404, JSON, errorJson("找不到文件"))
                    return
                }
                onCurrent(id)
                writeBytes(output, 200, JSON, indexJson().toByteArray(UTF_8))
            }
            head.method == "DELETE" && parts.size == 4 && parts[1] == "api" && parts[2] == "backgrounds" -> {
                if (!authorized(head, output)) return
                val id = decodePart(parts[3])
                val removingCurrent = currentId() == id
                if (!library.delete(id)) {
                    writeBytes(output, 404, JSON, errorJson("找不到文件"))
                    return
                }
                if (removingCurrent) onCurrent(null)
                writeBytes(output, 200, JSON, indexJson().toByteArray(UTF_8))
            }
            head.method == "GET" && parts.size == 5 && parts[1] == "api" && parts[2] == "backgrounds" && parts[4] == "media" -> {
                if (!authorized(head, output)) return
                val file = library.file(decodePart(parts[3]))
                if (file == null) {
                    writeBytes(output, 404, JSON, errorJson("找不到文件"))
                    return
                }
                writeFile(output, file)
            }
            else -> writeBytes(output, 404, JSON, errorJson("没有这个地址"))
        }
    }

    private fun login(head: Head, input: InputStream, output: OutputStream) {
        val body = readBody(head, input, 8 * 1024)
        val session = gate.login(tokenFrom(head.headers["content-type"].orEmpty(), body))
        if (session == null) {
            writeBytes(output, 401, JSON, errorJson("口令不对"), listOf(CLEAR_COOKIE))
            return
        }
        writeBytes(output, 200, JSON, """{"ok":true}""".toByteArray(UTF_8), listOf(cookie(session)))
    }

    private fun logout(head: Head, output: OutputStream) {
        gate.logout(sessionOf(head))
        writeBytes(output, 200, JSON, """{"ok":true}""".toByteArray(UTF_8), listOf(CLEAR_COOKIE))
    }

    private fun upload(head: Head, input: InputStream, output: OutputStream) {
        val length = contentLength(head)
        if (length > BackgroundIntake.VIDEO_MAX_BYTES + 1024 * 1024) throw BackgroundRejected("文件太大")
        val type = head.headers["content-type"].orEmpty()
        if (!type.lowercase().contains("multipart/form-data")) throw BackgroundRejected("没有文件")
        val boundary = boundaryOf(type)
        val form = readForm(LimitedStream(input, length), boundary)
        try {
            val owner = BackgroundIntake.realName(form.fields["realname"].orEmpty())
            BackgroundIntake.inspect(form.file, form.filename)
            val stored = form.file.inputStream().use { source ->
                library.import(form.filename, source, form.file.length())
            }
            library.saveOwner(stored.name, owner)
            onCurrent(stored.name)
            writeBytes(output, 201, JSON, indexJson().toByteArray(UTF_8))
        } finally {
            form.file.delete()
        }
    }

    private fun authorized(head: Head, output: OutputStream): Boolean {
        if (gate.allow(sessionOf(head))) return true
        writeBytes(output, 401, JSON, errorJson("口令不对"))
        return false
    }

    private fun indexJson(): String {
        val current = currentId()?.let { jsonString(it) } ?: "null"
        val items = library.index().joinToString(",") { item ->
            val kind = if (item.kind == BackgroundKind.IMAGE) "image" else "video"
            """{"id":${jsonString(item.id)},"name":${jsonString(item.name)},"kind":"$kind","bytes":${item.bytes},"modified":${item.modified},"owner":${jsonString(item.owner)}}"""
        }
        return """{"current":$current,"items":[$items]}"""
    }

    private fun writeFile(output: OutputStream, file: File) {
        val header = StringBuilder()
        header.append("HTTP/1.1 200 OK\r\n")
        header.append("Content-Type: ${mediaType(file.name)}\r\n")
        header.append("Content-Length: ${file.length()}\r\n")
        header.append("Cache-Control: no-store\r\n")
        header.append("Connection: close\r\n\r\n")
        output.write(header.toString().toByteArray(StandardCharsets.ISO_8859_1))
        file.inputStream().use { it.copyTo(output) }
        output.flush()
    }

    companion object {
        const val PORT = 17920
        private val UTF_8 = StandardCharsets.UTF_8
        private const val JSON = "application/json; charset=utf-8"
        private const val CLEAR_COOKIE = "Set-Cookie: dock_bg=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax"

        private fun cookie(session: String) =
            "Set-Cookie: dock_bg=$session; HttpOnly; Path=/; SameSite=Lax"
    }
}

private data class Head(
    val method: String,
    val path: String,
    val headers: Map<String, String>,
)

private data class UploadedPart(
    val filename: String,
    val file: File,
    val fields: Map<String, String> = emptyMap(),
)

private fun readHead(input: InputStream): Head? {
    val request = readLine(input) ?: return null
    if (request.isBlank()) return null
    val bits = request.split(' ')
    if (bits.size < 2) return null
    val headers = linkedMapOf<String, String>()
    while (true) {
        val line = readLine(input) ?: break
        if (line.isEmpty()) break
        val colon = line.indexOf(':')
        if (colon > 0) headers[line.substring(0, colon).trim().lowercase()] = line.substring(colon + 1).trim()
    }
    return Head(bits[0].uppercase(), bits[1].substringBefore('?'), headers)
}

private fun readLine(input: InputStream): String? {
    val buffer = ByteArrayOutputStream()
    while (buffer.size() < 8192) {
        val value = input.read()
        if (value < 0) return if (buffer.size() == 0) null else utf8(buffer)
        if (value == '\n'.code) break
        if (value != '\r'.code) buffer.write(value)
    }
    return utf8(buffer)
}

private fun utf8(buffer: ByteArrayOutputStream): String = String(buffer.toByteArray(), Charsets.UTF_8)

private fun contentLength(head: Head): Long {
    return head.headers["content-length"]?.toLongOrNull() ?: 0L
}

private fun readBody(head: Head, input: InputStream, max: Int): String {
    val length = contentLength(head)
    if (length > max) throw BackgroundRejected("请求不完整")
    val bytes = ByteArray(length.toInt())
    var offset = 0
    while (offset < bytes.size) {
        val count = input.read(bytes, offset, bytes.size - offset)
        if (count < 0) throw BackgroundRejected("请求不完整")
        offset += count
    }
    return String(bytes, Charsets.UTF_8)
}

private fun discardBody(head: Head, input: InputStream) {
    var left = contentLength(head)
    val buffer = ByteArray(8192)
    while (left > 0) {
        val count = input.read(buffer, 0, minOf(buffer.size.toLong(), left).toInt())
        if (count < 0) return
        left -= count
    }
}

private fun sessionOf(head: Head): String? {
    val cookie = head.headers["cookie"] ?: return null
    return cookie.split(';').map { it.trim() }.firstNotNullOfOrNull { part ->
        if (part.startsWith("dock_bg=")) part.removePrefix("dock_bg=").ifBlank { null } else null
    }
}

private fun tokenFrom(contentType: String, body: String): String {
    if (contentType.lowercase().contains("application/json")) {
        val match = Regex(""""token"\s*:\s*"((?:\\.|[^"\\])*)"""").find(body) ?: return ""
        return match.groupValues[1].replace("\\\"", "\"").replace("\\\\", "\\")
    }
    return body.split('&').firstNotNullOfOrNull { part ->
        val key = urlDecode(part.substringBefore('='))
        if (key == "token") urlDecode(part.substringAfter('=')) else null
    }.orEmpty()
}

private fun boundaryOf(contentType: String): String {
    val match = Regex("""boundary=(?:"([^"]+)"|([^;\s]+))""").find(contentType)
        ?: throw BackgroundRejected("没有文件")
    return match.groupValues[1].ifBlank { match.groupValues[2] }
}

private fun readForm(input: InputStream, boundary: String): UploadedPart {
    val source = PushbackStream(input)
    val marker = "--$boundary"
    while (true) {
        val line = readLine(source) ?: throw BackgroundRejected("没有文件")
        if (line == marker) break
        if (line == "$marker--") throw BackgroundRejected("没有文件")
    }
    var filename = ""
    var media: File? = null
    val fields = linkedMapOf<String, String>()
    val pattern = "\r\n--$boundary".toByteArray(StandardCharsets.ISO_8859_1)
    try {
        while (true) {
            var field = ""
            var partName = ""
            while (true) {
                val line = readLine(source) ?: throw BackgroundRejected("没有文件")
                if (line.isEmpty()) break
                if (line.lowercase().startsWith("content-disposition:")) {
                    field = fieldName(line)
                    partName = filenameFrom(line)
                }
            }
            val tmp = File.createTempFile("dock-bg-", ".upload")
            try {
                tmp.outputStream().use { out -> copyUntil(source, pattern, out) }
            } catch (error: Exception) {
                tmp.delete()
                throw error
            }
            if (partName.isNotBlank()) {
                media?.delete()
                filename = partName
                media = tmp
            } else {
                if (field.isNotBlank()) fields[field] = tmp.readText(Charsets.UTF_8).trim()
                tmp.delete()
            }
            val tail = readLine(source) ?: break
            if (tail == "--") break
            if (tail.isNotEmpty()) throw BackgroundRejected("请求不完整")
        }
    } catch (error: Exception) {
        media?.delete()
        throw error
    }
    val stored = media ?: throw BackgroundRejected("没有文件")
    if (filename.isBlank()) {
        stored.delete()
        throw BackgroundRejected("没有文件")
    }
    return UploadedPart(filename, stored, fields)
}

private fun fieldName(header: String): String {
    return Regex("""\bname="([^"]*)"""").find(header)?.groupValues?.get(1).orEmpty()
}

private fun filenameFrom(header: String): String {
    val encoded = Regex("""filename\*=(?:UTF-8|utf-8)''([^;\s]+)""", RegexOption.IGNORE_CASE).find(header)
    if (encoded != null) return urlDecode(encoded.groupValues[1])
    val quoted = Regex("""filename="([^"]*)"""").find(header)
    if (quoted != null) return quoted.groupValues[1]
    return ""
}

private fun copyUntil(input: PushbackStream, pattern: ByteArray, out: OutputStream) {
    val chunk = ByteArray(64 * 1024)
    var pending = ByteArray(0)
    while (true) {
        val count = input.read(chunk)
        if (count < 0) throw BackgroundRejected("请求不完整")
        val data = pending + chunk.copyOf(count)
        val at = indexOf(data, pattern)
        if (at >= 0) {
            out.write(data, 0, at)
            val after = at + pattern.size
            if (after < data.size) input.unread(data, after, data.size)
            return
        }
        val keep = pattern.size - 1
        if (data.size > keep) {
            out.write(data, 0, data.size - keep)
            pending = data.copyOfRange(data.size - keep, data.size)
        } else {
            pending = data
        }
    }
}

private fun indexOf(data: ByteArray, pattern: ByteArray): Int {
    if (pattern.isEmpty() || data.size < pattern.size) return -1
    val last = data.size - pattern.size
    for (start in 0..last) {
        var matched = true
        for (offset in pattern.indices) {
            if (data[start + offset] != pattern[offset]) {
                matched = false
                break
            }
        }
        if (matched) return start
    }
    return -1
}

private fun decodePart(value: String): String = urlDecode(value)

private fun urlDecode(value: String): String = URLDecoder.decode(value, "UTF-8")

private fun mediaType(name: String): String {
    return when (name.substringAfterLast('.', "").lowercase()) {
        "jpg", "jpeg" -> "image/jpeg"
        "png" -> "image/png"
        "webp" -> "image/webp"
        "gif" -> "image/gif"
        "mp4" -> "video/mp4"
        "webm" -> "video/webm"
        "mkv" -> "video/x-matroska"
        "mov" -> "video/quicktime"
        else -> "application/octet-stream"
    }
}

private fun errorJson(message: String) = """{"error":${jsonString(message)}}""".toByteArray(StandardCharsets.UTF_8)

private fun jsonString(value: String): String {
    val buffer = StringBuilder("\"")
    value.forEach { char ->
        when (char) {
            '\\' -> buffer.append("\\\\")
            '"' -> buffer.append("\\\"")
            '\n' -> buffer.append("\\n")
            '\r' -> buffer.append("\\r")
            else -> buffer.append(char)
        }
    }
    buffer.append('"')
    return buffer.toString()
}

private fun writeBytes(
    output: OutputStream,
    status: Int,
    contentType: String,
    body: ByteArray,
    extra: List<String> = emptyList(),
) {
    val reason = when (status) {
        200 -> "OK"
        201 -> "Created"
        400 -> "Bad Request"
        401 -> "Unauthorized"
        404 -> "Not Found"
        else -> "Error"
    }
    val header = StringBuilder()
    header.append("HTTP/1.1 $status $reason\r\n")
    header.append("Content-Type: $contentType\r\n")
    header.append("Content-Length: ${body.size}\r\n")
    header.append("Cache-Control: no-store\r\n")
    extra.forEach { header.append(it).append("\r\n") }
    header.append("Connection: close\r\n\r\n")
    output.write(header.toString().toByteArray(StandardCharsets.ISO_8859_1))
    output.write(body)
    output.flush()
}

private class PushbackStream(private val input: InputStream) : InputStream() {
    private val unread = ArrayDeque<Byte>()

    fun unread(bytes: ByteArray, from: Int, until: Int) {
        for (index in until - 1 downTo from) unread.addFirst(bytes[index])
    }

    override fun read(): Int {
        if (unread.isNotEmpty()) return unread.removeFirst().toInt() and 0xff
        return input.read()
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        var copied = 0
        while (copied < length && unread.isNotEmpty()) {
            buffer[offset + copied] = unread.removeFirst()
            copied += 1
        }
        if (copied == length) return copied
        val rest = input.read(buffer, offset + copied, length - copied)
        if (rest < 0) return if (copied == 0) -1 else copied
        return copied + rest
    }
}

private class LimitedStream(private val input: InputStream, private var left: Long) : InputStream() {
    override fun read(): Int {
        if (left <= 0) return -1
        val value = input.read()
        if (value >= 0) left -= 1
        return value
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (left <= 0) return -1
        val count = input.read(buffer, offset, minOf(length.toLong(), left).toInt())
        if (count > 0) left -= count
        return count
    }
}
