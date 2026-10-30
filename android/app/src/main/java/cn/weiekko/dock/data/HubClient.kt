package cn.weiekko.dock.data

import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import okhttp3.Call
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

class HubClient(
    private val http: OkHttpClient = defaultClient(),
    private val json: Json = defaultJson(),
) {
    private val clients = ConcurrentHashMap<Long, OkHttpClient>()
    private val activeChat = AtomicReference<Call?>(null)

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

    fun snapshot(connection: HubConnection, since: Int? = null, waitSec: Int = 0): Snapshot {
        // since + wait 是长轮询：Hub 状态没变就挂着，变了立刻回。
        // 旧版 Hub 会忽略这两个参数、秒回一个没有 seq 的 snapshot，调用方据此回退到普通轮询。
        val longPoll = since != null && waitSec > 0
        val path = if (longPoll) "/v1/snapshot?since=$since&wait=$waitSec" else "/v1/snapshot"
        val timeout = if (longPoll) (waitSec + 5) * 1000L else SNAPSHOT_TIMEOUT_MS
        val request = authorized(connection, path).get().build()
        val body = execute(request, readTimeoutMs = timeout)
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
        val call = clientFor(COMPANION_TIMEOUT_MS).newCall(request)
        // 单飞：新一轮先把上一轮没回来的那条掐掉，别让两份 LLM 流同时回来抢字幕
        activeChat.getAndSet(call)?.cancel()
        try {
            return decode(readText(call))
        } finally {
            activeChat.compareAndSet(call, null)
        }
    }

    /**
     * 打断时调用：`execute()` 是阻塞的，只 cancel 协程停不下这条 HTTP，
     * 要等 90s 超时才还线程。这里直接 cancel 掉 call，线程立刻回来。
     */
    fun cancelActiveChat() {
        activeChat.getAndSet(null)?.cancel()
    }

    fun companionStop(connection: HubConnection) {
        cancelActiveChat()
        val request = authorized(connection, "/v1/companion/stop")
            .post("{}".toRequestBody(JSON))
            .build()
        execute(request, readTimeoutMs = STOP_TIMEOUT_MS, expectAuthError = false)
    }

    /**
     * 对讲（F33 正向）：手机上的一句话直接从书桌喇叭出来，不走 LLM。
     * 语音侧的「对讲，……」捷径也落在这一条。
     */
    fun companionAnnounce(connection: HubConnection, text: String): CompanionChatResponse {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) throw HubException("bad_request", "text 不能为空")
        val payload = buildJsonObject {
            put("text", JsonPrimitive(trimmed.take(200)))
        }
        val request = authorized(connection, "/v1/companion/announce")
            .post(json.encodeToString(JsonObject.serializer(), payload).toRequestBody(JSON))
            .build()
        val body = execute(request, readTimeoutMs = COMMAND_TIMEOUT_MS)
        return decode(body)
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

    /**
     * 通知镜像（F35）：把一条白名单通知送到 Hub，书桌屏幕亮一下。
     * 失败会抛给调用方——监听器那边 runCatching 兜住就行，消息不值得连累服务。
     */
    fun mirror(connection: HubConnection, app: String, title: String, text: String) {
        val payload = buildJsonObject {
            put("app", JsonPrimitive(app.trim().take(80)))
            put("title", JsonPrimitive(title.trim().take(60)))
            put("text", JsonPrimitive(text.trim().take(200)))
        }
        val request = authorized(connection, "/v1/mirror")
            .post(json.encodeToString(JsonObject.serializer(), payload).toRequestBody(JSON))
            .build()
        execute(request, readTimeoutMs = COMMAND_TIMEOUT_MS)
    }

    /** 她记得的事（F28）：列表给设置页「她记得的事」分区。 */
    fun companionMemory(connection: HubConnection): MemoryList {
        val request = authorized(connection, "/v1/companion/memory").get().build()
        val body = execute(request, readTimeoutMs = COMMAND_TIMEOUT_MS)
        return decode(body)
    }

    /** 删一条记忆。404 说明这条已经不在了，当成功处理。 */
    fun companionMemoryDelete(connection: HubConnection, key: String) {
        val encoded = java.net.URLEncoder.encode(key, "UTF-8")
        val request = authorized(connection, "/v1/companion/memory/$encoded")
            .delete()
            .build()
        execute(request, readTimeoutMs = COMMAND_TIMEOUT_MS)
    }

    /**
     * 在桌上报（F24）：away_sec 是这次离座了多少秒（present=false 时省略），
     * 语义见 lan-protocol §6.13。失败静默——下次边沿再报。
     */
    fun reportDesk(connection: HubConnection, present: Boolean, awaySec: Int? = null) {
        val payload = buildJsonObject {
            put("present", JsonPrimitive(present))
            if (awaySec != null) put("away_sec", JsonPrimitive(awaySec))
        }
        val request = authorized(connection, "/v1/at-desk")
            .post(json.encodeToString(JsonObject.serializer(), payload).toRequestBody(JSON))
            .build()
        execute(request, readTimeoutMs = COMMAND_TIMEOUT_MS, expectAuthError = false)
    }

    /** 一张照片的原图。id 只认 basename，服务端对穿越一律 404。 */
    fun photo(connection: HubConnection, id: String): ByteArray {
        val safe = id.trim()
        if (safe.isEmpty() || safe.contains('/') || safe.contains('\\') || safe == "..") {
            throw HubException("bad_request", "照片 id 无效")
        }
        val encoded = java.net.URLEncoder.encode(safe, "UTF-8")
        val request = authorized(connection, "/v1/photos/$encoded")
            .header("Accept", "image/*")
            .get()
            .build()
        return executeBytes(request, readTimeoutMs = SNAPSHOT_TIMEOUT_MS)
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

    /** 每种超时复用同一个 client：newBuilder().build() 每次都会多一份连接池配置拷贝。 */
    private fun clientFor(readTimeoutMs: Long): OkHttpClient =
        clients.getOrPut(readTimeoutMs) {
            http.newBuilder()
                .readTimeout(readTimeoutMs, TimeUnit.MILLISECONDS)
                .callTimeout(readTimeoutMs + 1_000, TimeUnit.MILLISECONDS)
                .build()
        }

    private fun execute(
        request: Request,
        readTimeoutMs: Long,
        expectAuthError: Boolean = true,
    ): String = readText(clientFor(readTimeoutMs).newCall(request), expectAuthError)

    private fun executeBytes(
        request: Request,
        readTimeoutMs: Long,
        expectAuthError: Boolean = true,
    ): ByteArray = readBytes(clientFor(readTimeoutMs).newCall(request), expectAuthError)

    private fun readText(call: Call, expectAuthError: Boolean = true): String {
        val response = try {
            call.execute()
        } catch (e: IOException) {
            throw HubNetworkException("连不上 Hub：${e.message ?: "网络错误"}", e)
        }
        response.use { resp ->
            val raw = resp.body?.string().orEmpty()
            if (resp.isSuccessful) return raw
            throw parseError(raw, resp.code, expectAuthError)
        }
    }

    private fun readBytes(call: Call, expectAuthError: Boolean = true): ByteArray {
        val response = try {
            call.execute()
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
