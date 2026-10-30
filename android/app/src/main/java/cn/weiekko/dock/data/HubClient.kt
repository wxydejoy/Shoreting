package cn.weiekko.dock.data

import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

class HubClient(
    private val http: OkHttpClient = defaultClient(),
    private val json: Json = defaultJson(),
) {
    fun health(connection: HubConnection): Health {
        // 注意：Hub 完全可以就跑在 Mini 上（此时 Hub 的 host 与 Mini 的 host 相同）。
        // 以前这里用 IP 判断「填成 Mini 了」，结果在这种部署下必然抛异常，
        // HubWatchService 永远拿不到 ok=true，休眠中的 App 就再也醒不过来（黑屏卡死）。
        // 判断是否连错服务，应该看 /health 返回的 service / name。
        val request = Request.Builder()
            .url("${connection.baseUrl()}/health")
            .get()
            .build()
        val body = execute(request, readTimeoutMs = SNAPSHOT_TIMEOUT_MS, expectAuthError = false)
        val health = decode<Health>(body)
        if (health.service != "dock-hub") {
            throw HubException("bad_request", "这不是岸亭 Hub（service=${health.service}）")
        }
        if (health.name.equals("mini", ignoreCase = true)) {
            throw HubException(
                "bad_request",
                "连到了 Mini 的监控服务，不是岸亭 Hub（Hub 端口一般是 17890）",
            )
        }
        if (health.protocol < 1) {
            throw HubException("bad_request", "Hub 协议版本过低：${health.protocol}")
        }
        return health
    }

    fun miniHealth(connection: MiniConnection): Health {
        val request = Request.Builder()
            .url("${connection.baseUrl()}/health")
            .get()
            .build()
        val body = execute(request, readTimeoutMs = SNAPSHOT_TIMEOUT_MS, expectAuthError = false)
        val health = decode<Health>(body)
        if (health.service != "helm-mini") {
            throw HubException("bad_request", "这不是岸亭 Mini（service=${health.service}）")
        }
        if (health.protocol < 1) {
            throw HubException("bad_request", "Mini 协议版本过低：${health.protocol}")
        }
        return health
    }

    fun miniSnapshot(connection: MiniConnection): PcStatus {
        val request = miniRequest(connection, "/v1/snapshot").get().build()
        val body = execute(request, readTimeoutMs = SNAPSHOT_TIMEOUT_MS)
        val parsed = decode<MiniSnapshot>(body)
        return parsed.pc ?: PcStatus(online = false, updatedAt = "")
    }

    fun snapshot(connection: HubConnection): Snapshot {
        val request = authorized(connection, "/v1/snapshot").get().build()
        val body = execute(request, readTimeoutMs = SNAPSHOT_TIMEOUT_MS)
        return decode(body)
    }

    /** 天气由 Hub 代取：手机可能连在只有局域网的 Wi-Fi 上，自己出不了外网。 */
    fun weather(connection: HubConnection, city: String): WeatherInfo {
        val trimmed = city.trim()
        if (trimmed.isEmpty()) throw HubException("bad_request", "城市为空")
        val encoded = java.net.URLEncoder.encode(trimmed, "UTF-8")
        val request = authorized(connection, "/v1/weather?city=$encoded").get().build()
        val body = execute(request, readTimeoutMs = SNAPSHOT_TIMEOUT_MS)
        return decode(body)
    }

    fun command(
        connection: HubConnection,
        deviceId: String,
        on: Boolean? = null,
        brightness: Int? = null,
        run: Boolean? = null,
    ): HubDevice {
        if (on == null && brightness == null && run == null) {
            throw HubException("bad_request", "命令不能为空")
        }
        val payload = buildJsonObject {
            if (on != null) put("on", JsonPrimitive(on))
            if (brightness != null) put("brightness", JsonPrimitive(brightness))
            if (run != null) put("run", JsonPrimitive(run))
        }
        val request = authorized(connection, "/v1/devices/$deviceId/command")
            .post(json.encodeToString(JsonObject.serializer(), payload).toRequestBody(JSON))
            .build()
        val body = execute(request, readTimeoutMs = COMMAND_TIMEOUT_MS)
        return decode(body)
    }

    fun mediaCommand(connection: HubConnection, action: String): MediaInfo {
        val payload = buildJsonObject {
            put("media", JsonPrimitive(action))
        }
        val request = authorized(connection, "/v1/devices/media/command")
            .post(json.encodeToString(JsonObject.serializer(), payload).toRequestBody(JSON))
            .build()
        val body = execute(request, readTimeoutMs = COMMAND_TIMEOUT_MS)
        return decode(body)
    }

    fun companionChat(
        connection: HubConnection,
        text: String,
        turnId: String = "",
    ): CompanionChatResponse {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) {
            throw HubException("bad_request", "text 不能为空")
        }
        val payload = buildJsonObject {
            put("text", JsonPrimitive(trimmed))
            if (turnId.isNotBlank()) put("turn_id", JsonPrimitive(turnId.trim()))
        }
        val request = authorized(connection, "/v1/companion/chat")
            .post(json.encodeToString(JsonObject.serializer(), payload).toRequestBody(JSON))
            .build()
        val body = execute(request, readTimeoutMs = COMPANION_TIMEOUT_MS)
        return decode(body)
    }

    fun companionStop(connection: HubConnection) {
        val request = authorized(connection, "/v1/companion/stop")
            .post("{}".toRequestBody(JSON))
            .build()
        execute(request, readTimeoutMs = STOP_TIMEOUT_MS, expectAuthError = false)
    }

    /** 在场上报（F25）：state 只有 home / away，语义见 lan-protocol §6.12。 */
    fun reportPresence(connection: HubConnection, state: String) {
        val payload = buildJsonObject {
            put("state", JsonPrimitive(state))
        }
        val request = authorized(connection, "/v1/presence")
            .post(json.encodeToString(JsonObject.serializer(), payload).toRequestBody(JSON))
            .build()
        execute(request, readTimeoutMs = COMMAND_TIMEOUT_MS)
    }

    fun companionAudio(connection: HubConnection, audioId: String): ByteArray {
        val id = audioId.trim()
        if (!AUDIO_ID.matches(id)) {
            throw HubException("bad_request", "audio_id 无效")
        }
        val request = authorized(connection, "/v1/companion/audio/$id")
            .header("Accept", "audio/wav")
            .get()
            .build()
        return executeBytes(request, readTimeoutMs = COMPANION_TIMEOUT_MS)
    }

    private fun authorized(connection: HubConnection, path: String): Request.Builder {
        return Request.Builder()
            .url("${connection.baseUrl()}$path")
            .header("Authorization", "Bearer ${connection.token}")
            .header("Accept", "application/json")
    }

    private fun miniRequest(connection: MiniConnection, path: String): Request.Builder {
        val builder = Request.Builder()
            .url("${connection.baseUrl()}$path")
            .header("Accept", "application/json")
        if (connection.token.isNotBlank()) {
            builder.header("Authorization", "Bearer ${connection.token}")
        }
        return builder
    }

    private fun execute(
        request: Request,
        readTimeoutMs: Long,
        expectAuthError: Boolean = true,
    ): String {
        val callClient = http.newBuilder()
            .readTimeout(readTimeoutMs, TimeUnit.MILLISECONDS)
            .callTimeout(readTimeoutMs + 1_000, TimeUnit.MILLISECONDS)
            .build()
        val response = try {
            callClient.newCall(request).execute()
        } catch (e: IOException) {
            throw HubNetworkException("连不上 Hub：${e.message ?: "网络错误"}", e)
        }
        response.use { resp ->
            val raw = resp.body?.string().orEmpty()
            if (resp.isSuccessful) return raw
            throw parseError(raw, resp.code, expectAuthError)
        }
    }

    private fun executeBytes(
        request: Request,
        readTimeoutMs: Long,
        expectAuthError: Boolean = true,
    ): ByteArray {
        val callClient = http.newBuilder()
            .readTimeout(readTimeoutMs, TimeUnit.MILLISECONDS)
            .callTimeout(readTimeoutMs + 1_000, TimeUnit.MILLISECONDS)
            .build()
        val response = try {
            callClient.newCall(request).execute()
        } catch (e: IOException) {
            throw HubNetworkException("连不上 Hub：${e.message ?: "网络错误"}", e)
        }
        response.use { resp ->
            val bytes = resp.body?.bytes() ?: ByteArray(0)
            if (resp.isSuccessful) return bytes
            val raw = bytes.toString(Charsets.UTF_8)
            throw parseError(raw, resp.code, expectAuthError)
        }
    }

    private fun parseError(raw: String, httpStatus: Int, expectAuthError: Boolean): HubException {
        val parsed = runCatching { json.decodeFromString<ErrorBody>(raw) }.getOrNull()
        val code = parsed?.error?.code
            ?: when (httpStatus) {
                401 -> "unauthorized"
                404 -> "not_found"
                409 -> "offline"
                503 -> "login_required"
                502 -> "mijia_error"
                else -> "mijia_error"
            }
        val message = parsed?.error?.message ?: defaultMessage(code, httpStatus)
        if (!expectAuthError && httpStatus == 401) {
            return HubException(code, message, httpStatus)
        }
        return HubException(code, message, httpStatus)
    }

    private inline fun <reified T> decode(raw: String): T {
        return try {
            json.decodeFromString<T>(raw)
        } catch (e: Exception) {
            throw HubException("bad_request", "Hub 返回无法解析：${e.message}")
        }
    }

    companion object {
        const val SNAPSHOT_TIMEOUT_MS = 5_000L
        const val COMMAND_TIMEOUT_MS = 10_000L
        const val COMPANION_TIMEOUT_MS = 90_000L
        const val STOP_TIMEOUT_MS = 2_000L
        private val AUDIO_ID = Regex("^[A-Za-z0-9._-]+$")
        private val JSON = "application/json; charset=utf-8".toMediaType()

        @OptIn(ExperimentalSerializationApi::class)
        fun defaultJson(): Json = Json {
            ignoreUnknownKeys = true
            isLenient = true
            coerceInputValues = true
            encodeDefaults = false
            namingStrategy = kotlinx.serialization.json.JsonNamingStrategy.SnakeCase
        }

        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .writeTimeout(5, TimeUnit.SECONDS)
            .retryOnConnectionFailure(false)
            .build()

        fun defaultMessage(code: String, httpStatus: Int): String = when (code) {
            "unauthorized" -> "Token 不正确"
            "not_found" -> "设备不存在"
            "offline" -> "设备离线"
            "login_required" -> "请在电脑上扫码登录米家"
            "action_error" -> "无法启动程序"
            "media_error" -> "无法控制播放"
            "companion_unavailable" -> "伴侣暂时无法回答"
            "unsupported" -> "该设备不支持此操作"
            "bad_request" -> "请求无效"
            else -> "Hub 错误 HTTP $httpStatus"
        }
    }
}
