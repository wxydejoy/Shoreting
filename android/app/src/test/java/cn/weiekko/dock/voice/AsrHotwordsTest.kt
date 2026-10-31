package cn.weiekko.dock.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AsrHotwordsTest {
    @Test
    fun chineseSplitsPerChar() {
        assertEquals("岸 宝", AsrHotwords.toTokens("岸宝"))
        assertEquals("无 畏 契 约", AsrHotwords.toTokens("无畏契约"))
        assertEquals("嘿 岸 宝", AsrHotwords.toTokens("嘿岸宝"))
    }

    @Test
    fun latinStaysTogether() {
        assertEquals("Steam", AsrHotwords.toTokens("Steam"))
        assertEquals("开 Steam", AsrHotwords.toTokens("开Steam"))
    }

    @Test
    fun streamJoinsWithNewline() {
        val stream = AsrHotwords.forStream()
        assertTrue(stream.contains("岸 宝"))
        assertTrue(stream.contains("无 畏 契 约"))
        // sherpa 的热词文件每行一个词；用 / 拼会把整行当成一个词，全部编码失败
        assertTrue(stream.contains("\n"))
        assertTrue(!stream.contains("/"))
    }

    @Test
    fun extraAppearsInStream() {
        AsrHotwords.setExtra(listOf("书桌灯", "  ", "岸宝"))
        val stream = AsrHotwords.forStream()
        assertTrue(stream.contains("书 桌 灯"))
        AsrHotwords.setExtra(emptyList())
    }

    @Test
    fun mixedWordsJoinTheStream() {
        // F6 中英混说：口语常夹的英文词直接进热词表
        val stream = AsrHotwords.forStream()
        assertTrue(stream.contains("GitHub"))
        assertTrue(stream.contains("Steam"))
        assertTrue(stream.contains("PPT"))
    }

    @Test
    fun dropsOutOfVocabPhrases() {
        val known = setOf("台", "灯", "空", "调")
        val stream = AsrHotwords.forStream(known)
        assertTrue(stream.contains("台 灯"))
        assertTrue(stream.contains("空 调"))
        assertTrue(!stream.contains("岸"))
        assertTrue(!stream.contains("Steam"))
    }

    @Test
    fun versionOnlyMovesWhenTheVocabularyChanges() {
        AsrHotwords.setExtra(emptyList())
        val before = AsrHotwords.version
        AsrHotwords.setExtra(listOf("书桌灯"))
        val after = AsrHotwords.version
        assertTrue(after > before)
        // 同一份词表再说一次（还有空白和重复），不该让语音服务白装一遍识别器
        AsrHotwords.setExtra(listOf("书桌灯", "书桌灯", "  "))
        assertEquals(after, AsrHotwords.version)
        AsrHotwords.setExtra(emptyList())
        assertTrue(AsrHotwords.version > after)
    }

    @Test
    fun parseTokensTakesFirstColumn() {
        val known = AsrHotwords.parseTokens("<blk> 0\n台 100\n灯 101\n")
        assertTrue(known.contains("台"))
        assertTrue(known.contains("灯"))
        assertTrue(!known.contains("100"))
    }
}
