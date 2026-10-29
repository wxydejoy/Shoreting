package cn.weiekko.dock.background

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayInputStream

class BackgroundLibraryTest {
    @get:Rule
    val folder = TemporaryFolder()

    @Test
    fun indexListsOnlyMediaAndNewestFirst() {
        val library = BackgroundLibrary(folder.root)
        library.import("older.png", bytes("png"), maxBytes = 100)
        library.import("newer.mp4", bytes("mp4"), maxBytes = 100)
        folder.root.resolve("notes.txt").writeText("skip")
        library.file("older.png")!!.setLastModified(1_000)
        library.file("newer.mp4")!!.setLastModified(2_000)

        val index = library.index()

        assertEquals(listOf("newer.mp4", "older.png"), index.map { it.id })
        assertEquals(BackgroundKind.VIDEO, index[0].kind)
        assertEquals(BackgroundKind.IMAGE, index[1].kind)
        assertEquals("newer.mp4", index[0].name)
        assertEquals(3L, index[0].bytes)
    }

    @Test
    fun importKeepsOriginalNameAndAvoidsCollisions() {
        val library = BackgroundLibrary(folder.root)
        val first = library.import("../../岸亭 夜景.PNG", bytes("one"), maxBytes = 100)
        val second = library.import("岸亭 夜景.PNG", bytes("two"), maxBytes = 100)

        assertEquals("岸亭 夜景.PNG", first.name)
        assertEquals("岸亭 夜景-2.PNG", second.name)
        assertTrue(library.file(first.name)!!.readBytes().contentEquals(bytes("one")))
        assertNull(library.file("../secret.png"))
    }

    @Test
    fun rejectsEmptyUnknownAndOversizedFiles() {
        val library = BackgroundLibrary(folder.root)

        assertRejected("文件是空的") {
            library.import("a.png", ByteArrayInputStream(byteArrayOf()), maxBytes = 100)
        }
        assertRejected("只接受图片或视频") {
            library.import("a.txt", bytes("nope"), maxBytes = 100)
        }
        assertRejected("文件太大") {
            library.import("a.png", bytes("abcdef"), maxBytes = 3)
        }
        assertTrue(library.index().isEmpty())
    }

    @Test
    fun deleteRemovesIndexedFileOnly() {
        val library = BackgroundLibrary(folder.root)
        library.import("keep.jpg", bytes("jpg"), maxBytes = 100)
        library.import("drop.jpg", bytes("jpg"), maxBytes = 100)

        assertTrue(library.delete("drop.jpg"))
        assertFalse(library.delete("drop.jpg"))
        assertFalse(library.delete("../keep.jpg"))
        assertEquals(listOf("keep.jpg"), library.index().map { it.id })
    }

    @Test
    fun currentIdFollowsLibraryFileUri() {
        val library = BackgroundLibrary(folder.root)
        val imported = library.import("loop.mp4", bytes("mp4"), maxBytes = 100)
        val uri = imported.toURI().toString()

        assertEquals("loop.mp4", library.currentId(uri))
        assertNull(library.currentId("off"))
        assertNull(library.currentId(""))
        assertNull(library.currentId("content://media/1"))
    }

    private fun bytes(text: String) = text.toByteArray()

    private fun assertRejected(message: String, block: () -> Unit) {
        try {
            block()
        } catch (error: BackgroundRejected) {
            assertEquals(message, error.message)
            return
        }
        throw AssertionError("expected rejection: $message")
    }
}
