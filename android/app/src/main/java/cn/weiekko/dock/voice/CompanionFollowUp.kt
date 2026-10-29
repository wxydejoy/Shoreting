package cn.weiekko.dock.voice

object CompanionFollowUp {
    fun asks(text: String): Boolean {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return false
        return trimmed.contains('？') || trimmed.contains('?')
    }
}
