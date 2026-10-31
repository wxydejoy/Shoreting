package cn.weiekko.dock.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import cn.weiekko.dock.media.MirrorFilter
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.json.Json

private val Context.dataStore by preferencesDataStore(name = "dock_hub")

data class HubConnection(
    val host: String = DEFAULT_HOST,
    val port: Int = DEFAULT_PORT,
    val token: String = DEFAULT_TOKEN,
) {
    val isConfigured: Boolean
        get() = host.isNotBlank() && token.isNotBlank() && port in 1..65535

    fun baseUrl(): String = lanHttpUrl(host, port)

    fun isMiniHost(): Boolean = lanHost(host) == MiniConnection.DEFAULT_HOST

    companion object {
        const val DEFAULT_HOST = "10.83.22.121"
        const val DEFAULT_PORT = 17890
        const val DEFAULT_TOKEN = "knaksDr1lLdKiPJSokMttA3YbyMdeI9q1w7ULvoc_4g"
    }
}

data class MiniConnection(
    val host: String = DEFAULT_HOST,
    val port: Int = DEFAULT_PORT,
    val token: String = DEFAULT_TOKEN,
) {
    val isConfigured: Boolean
        get() = host.isNotBlank() && token.isNotBlank() && port in 1..65535

    fun baseUrl(): String = lanHttpUrl(host, port)

    companion object {
        const val DEFAULT_HOST = "10.83.22.121"
        const val DEFAULT_PORT = 17891
        const val DEFAULT_TOKEN = "helm-mini-weiekko"
    }
}

internal fun lanHttpUrl(host: String, port: Int): String = "http://${lanHost(host)}:$port"

internal fun lanHost(host: String): String {
    var cleaned = host.trim()
        .removePrefix("http://")
        .removePrefix("https://")
        .trimEnd('/')
    val slash = cleaned.indexOf('/')
    if (slash >= 0) cleaned = cleaned.substring(0, slash)
    val colon = cleaned.lastIndexOf(':')
    if (colon > 0) {
        val maybePort = cleaned.substring(colon + 1)
        if (maybePort.isNotEmpty() && maybePort.all { it.isDigit() }) {
            val parsed = maybePort.toIntOrNull()
            if (parsed != null && parsed in 1..65535) {
                cleaned = cleaned.substring(0, colon)
            }
        }
    }
    return cleaned
}

class HubPreferences(private val context: Context) {
    val connection: Flow<HubConnection> = context.dataStore.data.map { prefs ->
        HubConnection(
            host = prefs[KEY_HOST]?.takeIf { it.isNotBlank() } ?: HubConnection.DEFAULT_HOST,
            port = prefs[KEY_PORT] ?: HubConnection.DEFAULT_PORT,
            token = prefs[KEY_TOKEN]?.takeIf { it.isNotBlank() } ?: HubConnection.DEFAULT_TOKEN,
        )
    }

    val miniConnection: Flow<MiniConnection> = context.dataStore.data.map { prefs ->
        MiniConnection(
            host = prefs[KEY_MINI_HOST]?.takeIf { it.isNotBlank() } ?: MiniConnection.DEFAULT_HOST,
            port = prefs[KEY_MINI_PORT] ?: MiniConnection.DEFAULT_PORT,
            token = prefs[KEY_MINI_TOKEN]?.takeIf { it.isNotBlank() } ?: MiniConnection.DEFAULT_TOKEN,
        )
    }

    val videoUri: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[KEY_VIDEO_URI].orEmpty()
    }

    val tileLook: Flow<TileLook> = context.dataStore.data.map { prefs ->
        TileLook(
            style = TileStyle.fromId(prefs[KEY_TILE_STYLE]),
            opacityPercent = prefs[KEY_TILE_OPACITY] ?: TileLook().opacityPercent,
            cornerDp = prefs[KEY_TILE_CORNER] ?: TileLook().cornerDp,
        )
    }

    val typeLook: Flow<TypeLook> = context.dataStore.data.map { prefs ->
        TypeLook(
            font = DockFont.fromId(prefs[KEY_TYPE_FONT]),
            clockScalePercent = prefs[KEY_TYPE_CLOCK] ?: TypeLook().clockScalePercent,
            statsSize = prefs[KEY_TYPE_STATS] ?: TypeLook().statsSize,
            tileSize = prefs[KEY_TYPE_TILE] ?: TypeLook().tileSize,
            chipSize = prefs[KEY_TYPE_CHIP] ?: TypeLook().chipSize,
        )
    }

    val powerScreen: Flow<Boolean> = context.dataStore.data.map { prefs ->
        prefs[KEY_POWER_SCREEN] ?: true
    }

    val hubSleepDelaySec: Flow<Int> = context.dataStore.data.map { prefs ->
        prefs[KEY_HUB_SLEEP_DELAY_SEC] ?: DEFAULT_HUB_SLEEP_DELAY_SEC
    }

    val hubReconnectSec: Flow<Int> = context.dataStore.data.map { prefs ->
        prefs[KEY_HUB_RECONNECT_SEC] ?: DEFAULT_HUB_RECONNECT_SEC
    }

    val layout: Flow<DockLayout> = context.dataStore.data.map { prefs ->
        decodeLayout(prefs[KEY_LAYOUT].orEmpty())
    }

    val weatherEnabled: Flow<Boolean> = context.dataStore.data.map { prefs ->
        prefs[KEY_WEATHER_ENABLED] ?: true
    }

    val weatherCity: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[KEY_WEATHER_CITY] ?: DEFAULT_WEATHER_CITY
    }

    val weatherCache: Flow<WeatherInfo?> = context.dataStore.data.map { prefs ->
        decodeWeather(prefs[KEY_WEATHER_CACHE].orEmpty())
    }

    val wakeWordEnabled: Flow<Boolean> = context.dataStore.data.map { prefs ->
        prefs[KEY_WAKE_WORD] ?: true
    }

    val hubEnabled: Flow<Boolean> = context.dataStore.data.map { prefs ->
        prefs[KEY_HUB_ENABLED] ?: true
    }

    /** 在场检测（F24）：默认关。开了才要摄像头权限。 */
    val deskPresenceEnabled: Flow<Boolean> = context.dataStore.data.map { prefs ->
        prefs[KEY_DESK_PRESENCE] ?: false
    }

    /** 递话去重（F33/F4）：已经发过通知的最大 notify id。 */
    val lastNotifyId: Flow<Int> = context.dataStore.data.map { prefs ->
        prefs[KEY_LAST_NOTIFY_ID] ?: 0
    }

    /** 通知镜像（F35）：默认关——「哪些消息值得打断」很难调，宁可一开始不亮。 */
    val mirrorEnabled: Flow<Boolean> = context.dataStore.data.map { prefs ->
        prefs[KEY_MIRROR_ENABLED] ?: false
    }

    /** 通知镜像白名单：包名列表，设置页按分隔符随手填。 */
    val mirrorApps: Flow<List<String>> = context.dataStore.data.map { prefs ->
        MirrorFilter.parseAppList(prefs[KEY_MIRROR_APPS].orEmpty())
    }

    /** 镜像去重（F35）：已经亮过的最大 mirror id。 */
    val lastMirrorId: Flow<Int> = context.dataStore.data.map { prefs ->
        prefs[KEY_LAST_MIRROR_ID] ?: 0
    }

    suspend fun saveLastMirrorId(id: Int) {
        context.dataStore.edit { prefs ->
            if (id > (prefs[KEY_LAST_MIRROR_ID] ?: 0)) prefs[KEY_LAST_MIRROR_ID] = id
        }
    }

    suspend fun saveLastNotifyId(id: Int) {
        context.dataStore.edit { prefs ->
            if (id > (prefs[KEY_LAST_NOTIFY_ID] ?: 0)) prefs[KEY_LAST_NOTIFY_ID] = id
        }
    }

    suspend fun save(connection: HubConnection) {
        context.dataStore.edit { prefs ->
            prefs[KEY_HOST] = connection.host.trim()
            prefs[KEY_PORT] = connection.port
            prefs[KEY_TOKEN] = connection.token.trim()
        }
    }

    suspend fun saveMini(connection: MiniConnection) {
        context.dataStore.edit { prefs ->
            prefs[KEY_MINI_HOST] = connection.host.trim()
            prefs[KEY_MINI_PORT] = connection.port
            prefs[KEY_MINI_TOKEN] = connection.token.trim()
        }
    }

    suspend fun saveVideoUri(uri: String) {
        context.dataStore.edit { prefs ->
            prefs[KEY_VIDEO_URI] = uri
        }
    }

    suspend fun saveTileLook(look: TileLook) {
        context.dataStore.edit { prefs ->
            prefs[KEY_TILE_STYLE] = look.style.id
            prefs[KEY_TILE_OPACITY] = look.opacityPercent.coerceIn(TileLook.OPACITY_MIN, TileLook.OPACITY_MAX)
            prefs[KEY_TILE_CORNER] = look.cornerDp.coerceIn(TileLook.CORNER_MIN, TileLook.CORNER_MAX)
        }
    }

    suspend fun saveTypeLook(look: TypeLook) {
        context.dataStore.edit { prefs ->
            prefs[KEY_TYPE_FONT] = look.font.id
            prefs[KEY_TYPE_CLOCK] = look.clockScalePercent.coerceIn(TypeLook.CLOCK_MIN, TypeLook.CLOCK_MAX)
            prefs[KEY_TYPE_STATS] = look.statsSize.coerceIn(TypeLook.STATS_MIN, TypeLook.STATS_MAX)
            prefs[KEY_TYPE_TILE] = look.tileSize.coerceIn(TypeLook.TILE_MIN, TypeLook.TILE_MAX)
            prefs[KEY_TYPE_CHIP] = look.chipSize.coerceIn(TypeLook.CHIP_MIN, TypeLook.CHIP_MAX)
        }
    }

    suspend fun savePowerScreen(enabled: Boolean) {
        context.dataStore.edit { prefs ->
            prefs[KEY_POWER_SCREEN] = enabled
        }
    }

    suspend fun saveHubSleepDelaySec(sec: Int) {
        context.dataStore.edit { prefs ->
            prefs[KEY_HUB_SLEEP_DELAY_SEC] = sec.coerceIn(15, 600)
        }
    }

    suspend fun saveHubReconnectSec(sec: Int) {
        context.dataStore.edit { prefs ->
            prefs[KEY_HUB_RECONNECT_SEC] = sec.coerceIn(5, 120)
        }
    }

    suspend fun saveLayout(layout: DockLayout) {
        context.dataStore.edit { prefs ->
            prefs[KEY_LAYOUT] = LAYOUT_JSON.encodeToString(DockLayout.serializer(), layout)
        }
    }

    suspend fun saveWeatherEnabled(enabled: Boolean) {
        context.dataStore.edit { prefs ->
            prefs[KEY_WEATHER_ENABLED] = enabled
        }
    }

    suspend fun saveWeatherCity(city: String) {
        val next = city.trim()
        if (next.isEmpty()) return
        context.dataStore.edit { prefs ->
            prefs[KEY_WEATHER_CITY] = next
        }
    }

    suspend fun saveWeatherCache(info: WeatherInfo) {
        context.dataStore.edit { prefs ->
            prefs[KEY_WEATHER_CACHE] = LAYOUT_JSON.encodeToString(WeatherInfo.serializer(), info)
        }
    }

    suspend fun saveWakeWordEnabled(enabled: Boolean) {
        context.dataStore.edit { prefs ->
            prefs[KEY_WAKE_WORD] = enabled
        }
    }

    suspend fun saveHubEnabled(enabled: Boolean) {
        context.dataStore.edit { prefs ->
            prefs[KEY_HUB_ENABLED] = enabled
        }
    }

    suspend fun saveDeskPresenceEnabled(enabled: Boolean) {
        context.dataStore.edit { prefs ->
            prefs[KEY_DESK_PRESENCE] = enabled
        }
    }

    suspend fun saveMirrorEnabled(enabled: Boolean) {
        context.dataStore.edit { prefs ->
            prefs[KEY_MIRROR_ENABLED] = enabled
        }
    }

    suspend fun saveMirrorApps(raw: String) {
        context.dataStore.edit { prefs ->
            prefs[KEY_MIRROR_APPS] = MirrorFilter.parseAppList(raw).joinToString(",")
        }
    }

    companion object {
        private val KEY_HOST = stringPreferencesKey("host")
        private val KEY_PORT = intPreferencesKey("port")
        private val KEY_TOKEN = stringPreferencesKey("token")
        private val KEY_MINI_HOST = stringPreferencesKey("mini_host")
        private val KEY_MINI_PORT = intPreferencesKey("mini_port")
        private val KEY_MINI_TOKEN = stringPreferencesKey("mini_token")
        private val KEY_VIDEO_URI = stringPreferencesKey("background_video_uri")
        private val KEY_TILE_STYLE = stringPreferencesKey("tile_style")
        private val KEY_TILE_OPACITY = intPreferencesKey("tile_opacity")
        private val KEY_TILE_CORNER = intPreferencesKey("tile_corner")
        private val KEY_TYPE_FONT = stringPreferencesKey("type_font")
        private val KEY_TYPE_CLOCK = intPreferencesKey("type_clock")
        private val KEY_TYPE_STATS = intPreferencesKey("type_stats")
        private val KEY_TYPE_TILE = intPreferencesKey("type_tile")
        private val KEY_TYPE_CHIP = intPreferencesKey("type_chip")
        private val KEY_POWER_SCREEN = booleanPreferencesKey("power_screen")
        private val KEY_HUB_SLEEP_DELAY_SEC = intPreferencesKey("hub_sleep_delay_sec")
        private val KEY_HUB_RECONNECT_SEC = intPreferencesKey("hub_reconnect_sec")
        private val KEY_LAYOUT = stringPreferencesKey("home_layout")
        private val KEY_WEATHER_ENABLED = booleanPreferencesKey("weather_enabled")
        private val KEY_WEATHER_CITY = stringPreferencesKey("weather_city")
        private val KEY_WEATHER_CACHE = stringPreferencesKey("weather_cache")
        private val KEY_WAKE_WORD = booleanPreferencesKey("wake_word")
        private val KEY_HUB_ENABLED = booleanPreferencesKey("hub_enabled")
        private val KEY_DESK_PRESENCE = booleanPreferencesKey("desk_presence")
        private val KEY_LAST_NOTIFY_ID = intPreferencesKey("last_notify_id")
        private val KEY_MIRROR_ENABLED = booleanPreferencesKey("mirror_enabled")
        private val KEY_MIRROR_APPS = stringPreferencesKey("mirror_apps")
        private val KEY_LAST_MIRROR_ID = intPreferencesKey("last_mirror_id")

        const val DEFAULT_HUB_SLEEP_DELAY_SEC = 60
        const val DEFAULT_HUB_RECONNECT_SEC = 15
        const val DEFAULT_WEATHER_CITY = "上海"

        private val LAYOUT_JSON = Json {
            ignoreUnknownKeys = true
            encodeDefaults = true
        }

        private fun decodeLayout(raw: String): DockLayout {
            if (raw.isBlank()) return DockLayout()
            return runCatching { LAYOUT_JSON.decodeFromString(DockLayout.serializer(), raw) }
                .getOrElse { DockLayout() }
        }

        private fun decodeWeather(raw: String): WeatherInfo? {
            if (raw.isBlank()) return null
            return runCatching { LAYOUT_JSON.decodeFromString(WeatherInfo.serializer(), raw) }.getOrNull()
        }
    }
}
