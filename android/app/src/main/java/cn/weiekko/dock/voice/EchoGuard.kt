package cn.weiekko.dock.voice

/**
 * 朗读时的回声过滤（B2）。
 *
 * 念小说和「回你一句」不是一回事：几十个字一句、连着念半小时，喇叭里
 * 出来的声音被桌上的麦克风收回去，ASR 转出来同样是一长串中文——听起来
 * 跟人说话没区别，于是她又把自己念的段落当成「主人说的」，接一句
 * 「白色海洋。」再念下一句，一晚上就这么自问自答。实证：2026-11-01
 * 12:20–12:22 的聊天记录里，每 20 秒一条 70–98 字的「用户输入」，全是
 * 那本讲佳能白炮镜头的书。
 *
 * 人真的要在她念书时插话，喊的是「停一下」「别念了」这种几个字的短句；
 * 一整段几十字的长句几乎只能是回声。所以规则很简单：朗读期间，转写
 * 去掉空白后超过 [READING_ECHO_CHARS] 个字就整句丢掉，不喂给大脑。
 *
 * 纯逻辑，时间由调用方喂，单元测试直接给布尔。
 */
internal object EchoGuard {
    /** 朗读期间，长过这个字数（不含空白）就当成她自己的回声。 */
    const val READING_ECHO_CHARS = 14

    fun isEcho(reading: Boolean, text: String): Boolean {
        if (!reading) return false
        return text.count { !it.isWhitespace() } >= READING_ECHO_CHARS
    }

    /** 朗读期间允许过的短插话：去掉空白后的字数，给日志用。 */
    fun spokenChars(text: String): Int = text.count { !it.isWhitespace() }
}
