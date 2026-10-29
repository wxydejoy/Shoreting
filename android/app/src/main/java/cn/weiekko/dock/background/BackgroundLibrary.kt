package cn.weiekko.dock.background

import java.io.File
import java.io.InputStream
import java.net.URLDecoder

enum class BackgroundKind { IMAGE, VIDEO }

data class BackgroundFile(
    val id: String,
    val name: String,
    val kind: BackgroundKind,
    val bytes: Long,
    val modified: Long,
    val owner: String = "",
)

class BackgroundRejected(message: String) : Exception(message)

class BackgroundLibrary(private val root: File) {
    init {
        root.mkdirs()
    }

    fun index(): List<BackgroundFile> {
        return root.listFiles().orEmpty()
            .filter { it.isFile && kindOfName(it.name) != null }
            .sortedWith(compareByDescending<File> { it.lastModified() }.thenBy { it.name })
            .map { file ->
                BackgroundFile(
                    id = file.name,
                    name = file.name,
                    kind = kindOfName(file.name)!!,
                    bytes = file.length(),
                    modified = file.lastModified(),
                    owner = ownerOf(file.name),
                )
            }
    }

    fun import(originalName: String, data: ByteArray, maxBytes: Long): File {
        return import(originalName, data.inputStream(), maxBytes)
    }

    fun import(originalName: String, source: InputStream, maxBytes: Long): File {
        val stored = sanitize(originalName)
        if (kindOfName(stored) == null) throw BackgroundRejected("只接受图片或视频")
        val target = uniqueFile(stored)
        val partial = File(root, ".${target.name}.part")
        var written = 0L
        try {
            partial.outputStream().use { out ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val count = source.read(buffer)
                    if (count < 0) break
                    written += count
                    if (written > maxBytes) throw BackgroundRejected("文件太大")
                    out.write(buffer, 0, count)
                }
            }
            if (written == 0L) throw BackgroundRejected("文件是空的")
            if (!partial.renameTo(target)) {
                partial.copyTo(target, overwrite = true)
                partial.delete()
            }
            return target
        } catch (error: BackgroundRejected) {
            partial.delete()
            throw error
        }
    }

    fun file(id: String): File? {
        if (!isSafeId(id)) return null
        val candidate = File(root, id).canonicalFile
        if (candidate.parentFile != root.canonicalFile) return null
        if (!candidate.isFile || kindOfName(candidate.name) == null) return null
        return candidate
    }

    fun delete(id: String): Boolean {
        val target = file(id) ?: return false
        File(root, "$id.who").delete()
        return target.delete()
    }

    fun saveOwner(id: String, owner: String) {
        if (file(id) == null) return
        File(root, "$id.who").writeText(owner, Charsets.UTF_8)
    }

    private fun ownerOf(id: String): String {
        val note = File(root, "$id.who")
        if (!note.isFile) return ""
        return note.readText(Charsets.UTF_8).trim()
    }

    fun currentId(stored: String): String? {
        if (stored.isBlank() || stored == OFF) return null
        if (!stored.startsWith("file:")) return null
        val path = filePath(stored) ?: return null
        val candidate = File(path).canonicalFile
        if (candidate.parentFile != root.canonicalFile) return null
        if (!candidate.isFile || kindOfName(candidate.name) == null) return null
        return candidate.name
    }

    private fun uniqueFile(name: String): File {
        val ext = extension(name)
        val stem = if (ext.isEmpty()) name else name.dropLast(ext.length)
        var candidate = File(root, name)
        var number = 2
        while (candidate.exists()) {
            candidate = File(root, "$stem-$number$ext")
            number += 1
        }
        return candidate
    }

    private fun filePath(stored: String): String? {
        val raw = stored.removePrefix("file:")
        val slash = raw.indexOf('/')
        if (slash < 0) return null
        return URLDecoder.decode(raw.substring(slash), "UTF-8")
    }

    companion object {
        const val OFF = "off"
        const val MAX_BYTES = 512L * 1024 * 1024

        fun kindOfName(name: String): BackgroundKind? {
            return when (extension(name).lowercase()) {
                ".jpg", ".jpeg", ".png", ".webp", ".gif" -> BackgroundKind.IMAGE
                ".mp4", ".webm", ".mkv", ".mov" -> BackgroundKind.VIDEO
                else -> null
            }
        }

        fun backgroundKind(reference: String): BackgroundKind? {
            val tail = reference.substringBefore('?').substringAfterLast('/')
            val name = runCatching { URLDecoder.decode(tail, "UTF-8") }.getOrDefault(tail)
            return kindOfName(name)
        }

        private fun extension(name: String): String {
            val dot = name.lastIndexOf('.')
            if (dot <= 0 || dot == name.lastIndex) return ""
            return name.substring(dot)
        }

        private fun sanitize(original: String): String {
            val base = original.substringAfterLast('/').substringAfterLast('\\').trim()
            val cleaned = base.replace(Regex("""[\u0000-\u001F\\/:*?"<>|]"""), "").trim().trimEnd('.')
            if (cleaned.isBlank() || cleaned == "." || cleaned == "..") return "background"
            return cleaned
        }

        private fun isSafeId(id: String): Boolean {
            if (id.isBlank() || id.contains('/') || id.contains('\\') || id.contains("..")) return false
            return kindOfName(id) != null
        }
    }
}
