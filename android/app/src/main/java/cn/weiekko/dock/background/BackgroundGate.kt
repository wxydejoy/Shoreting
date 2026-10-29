package cn.weiekko.dock.background

import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

class BackgroundGate(private val token: () -> String) {
    private val sessions = ConcurrentHashMap<String, String>()

    fun login(presented: String): String? {
        val expected = token().trim()
        val given = presented.trim()
        if (expected.isEmpty() || given.isEmpty()) return null
        if (!sameToken(expected, given)) return null
        val session = UUID.randomUUID().toString()
        sessions[session] = expected
        return session
    }

    fun allow(session: String?): Boolean {
        if (session.isNullOrBlank()) return false
        val bound = sessions[session] ?: return false
        return sameToken(bound, token().trim())
    }

    fun logout(session: String?) {
        if (!session.isNullOrBlank()) sessions.remove(session)
    }

    private fun sameToken(expected: String, presented: String): Boolean {
        val digest = MessageDigest.getInstance("SHA-256")
        val left = digest.digest(expected.toByteArray(Charsets.UTF_8))
        val right = digest.digest(presented.toByteArray(Charsets.UTF_8))
        return MessageDigest.isEqual(left, right)
    }
}
