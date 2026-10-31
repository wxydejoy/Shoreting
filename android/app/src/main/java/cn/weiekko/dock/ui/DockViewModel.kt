package cn.weiekko.dock.ui
import cn.weiekko.dock.data.logConnection

import android.app.Application
import android.content.ContentResolver
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.provider.OpenableColumns
import cn.weiekko.dock.DockApp
import cn.weiekko.dock.background.BackgroundLibrary
import cn.weiekko.dock.background.BackgroundRejected
import android.os.SystemClock
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import cn.weiekko.dock.data.DeskLink
import cn.weiekko.dock.data.DemoSnapshot
import cn.weiekko.dock.data.deskLink
import cn.weiekko.dock.data.DeviceType
import cn.weiekko.dock.data.DockLayout
import cn.weiekko.dock.data.DockModule
import cn.weiekko.dock.data.Health
import cn.weiekko.dock.data.HubClient
import cn.weiekko.dock.data.HubConnection
import cn.weiekko.dock.data.HubDevice
import cn.weiekko.dock.data.HubException
import cn.weiekko.dock.data.HubNetworkException
import cn.weiekko.dock.data.HubPreferences
import cn.weiekko.dock.data.MediaInfo
import cn.weiekko.dock.data.MemoryEntry
import cn.weiekko.dock.data.MiniConnection
import cn.weiekko.dock.data.ModuleRect
import cn.weiekko.dock.relay.RelayNotifier
import cn.weiekko.dock.data.PcStatus
import cn.weiekko.dock.data.PresenceReporter
import cn.weiekko.dock.data.Scene
import cn.weiekko.dock.data.Snapshot
import android.graphics.BitmapFactory
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import cn.weiekko.dock.data.WeatherClient
import cn.weiekko.dock.data.WeatherInfo
import cn.weiekko.dock.data.WeatherPlaces
import cn.weiekko.dock.data.TileLook
import cn.weiekko.dock.data.TypeLook
import cn.weiekko.dock.voice.VoiceprintBus
import cn.weiekko.dock.voice.VoiceprintStore
import cn.weiekko.dock.data.WinApp
import cn.weiekko.dock.data.defaultDockLayout
import cn.weiekko.dock.data.deviceType
import cn.weiekko.dock.data.grow
import cn.weiekko.dock.data.isLoginRequired
import cn.weiekko.dock.data.nudge
import cn.weiekko.dock.data.snap
import cn.weiekko.dock.data.toWinApp
import cn.weiekko.dock.media.PhoneMedia
import cn.weiekko.dock.power.PresenceWatch
import cn.weiekko.dock.power.ScreenCommand
import cn.weiekko.dock.voice.AsrHotwords
import cn.weiekko.dock.voice.ConversationBus
import cn.weiekko.dock.voice.ConversationWindow
import cn.weiekko.dock.voice.CompanionVoice
import cn.weiekko.dock.voice.HelmLatency
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

data class DockUiState(
    val prefsReady: Boolean = false,
    val connection: HubConnection = HubConnection(),
    val hubEnabled: Boolean = true,
    val miniConnection: MiniConnection = MiniConnection(),
    val snapshot: Snapshot? = null,
    val miniPc: PcStatus? = null,
    val miniStale: Boolean = false,
    val stale: Boolean = false,
    val banner: String? = null,
    val busyIds: Set<String> = emptySet(),
    /** 正在跑的场景（F7）：跑着的按钮变省略号，再点忽略。 */
    val sceneBusy: Set<String> = emptySet(),
    val settingsStatus: String? = null,
    val settingsOk: Boolean = false,
    val testing: Boolean = false,
    val loading: Boolean = false,
    val preview: Boolean = true,
    val weather: WeatherInfo? = null,
    // F37 回忆相框：没数据可看的白天（电脑不在线、不在夜间压黑），轮播 Hub 的照片
    val framePhoto: ImageBitmap? = null,
    val weatherEnabled: Boolean = true,
    val weatherCity: String = HubPreferences.DEFAULT_WEATHER_CITY,
    val weatherError: String? = null,
    val winApps: List<WinApp> = DemoSnapshot.winApps,
    val selectedWinId: String? = null,
    val backgroundVideoUri: String? = null,
    val backgroundPageUrl: String? = null,
    val tileLook: TileLook = TileLook(),
    val typeLook: TypeLook = TypeLook(),
    val powerScreen: Boolean = true,
    val hubSleepDelaySec: Int = HubPreferences.DEFAULT_HUB_SLEEP_DELAY_SEC,
    val hubReconnectSec: Int = HubPreferences.DEFAULT_HUB_RECONNECT_SEC,
    val hubSleeping: Boolean = false,
    val deskPresenceEnabled: Boolean = false,
    val deskSleeping: Boolean = false,
    // F5 声纹：谁在说话。开关在本地；花名册和注册进度只服务设置页
    val voiceprintEnabled: Boolean = false,
    val voiceprintSpeakers: List<String> = emptyList(),
    val voiceprintEnroll: VoiceprintBus.EnrollState? = null,
    val mirrorEnabled: Boolean = false,
    val mirrorAppsRaw: String = "",
    // F35 通知镜像：白名单应用的通知，亮在屏幕上，30 秒自己收走
    val mirrorText: String? = null,
    val wakeWordEnabled: Boolean = true,
    // 免唤醒对话窗是冷状态（开关各一次），留在 UiState；热的听写走 voice 流
    val conversationOpen: Boolean = false,
    val layout: DockLayout = DockLayout(),
    val editing: Boolean = false,
    val selectedModule: DockModule? = null,
    val companionOpen: Boolean = false,
    val companionDraft: String = "",
    val companionHeard: String? = null,
    val companionReply: String? = null,
    val companionBusy: Boolean = false,
    val companionError: String? = null,
    val memoryItems: List<MemoryEntry> = emptyList(),
    val memoryLoading: Boolean = false,
    val memoryError: String? = null,
) {
    val configured: Boolean get() = connection.isConfigured
}

/**
 * 语音这一路单独走一条流：ASR 每出半句就更新一次（一句话里十几二十次），
 * 塞进 DockUiState 会让整棵界面跟着重组。同理 [DockViewModel.media] 每秒一次。
 */
data class VoiceState(
    val listening: Boolean = false,
    val text: String = "",
    val settled: Boolean = false,
)

class DockViewModel(application: Application) : AndroidViewModel(application) {
    private val prefs = HubPreferences(application)
    private val client = HubClient()
    private val weatherClient = WeatherClient()
    private val phoneMedia = PhoneMedia(application)
    private val companionVoice = CompanionVoice(application)
    private val presenceReporter = PresenceReporter(send = { state ->
        val connection = prefs.connection.first()
        if (!currentLink().pollsHub || !connection.isConfigured) return@PresenceReporter false
        withContext(Dispatchers.IO) {
            runCatching { client.reportPresence(connection, state) }.isSuccess
        }
    })
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    /** 默认网络的 Wi-Fi 状态变化直接喂给在场上报，不吃轮询节奏（Doze 也兜得住）。 */
    private fun registerNetworkWatch() {
        if (networkCallback != null) return
        val manager = getApplication<Application>()
            .getSystemService(ConnectivityManager::class.java) ?: return
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onCapabilitiesChanged(
                network: android.net.Network,
                capabilities: NetworkCapabilities,
            ) {
                presenceReporter.onWifiChanged(
                    capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI),
                    SystemClock.elapsedRealtime(),
                )
            }

            override fun onLost(network: android.net.Network) {
                presenceReporter.onWifiChanged(false, SystemClock.elapsedRealtime())
            }
        }
        runCatching { manager.registerDefaultNetworkCallback(callback) }
            .onSuccess { networkCallback = callback }
    }

    private fun unregisterNetworkWatch() {
        val callback = networkCallback ?: return
        networkCallback = null
        getApplication<Application>().getSystemService(ConnectivityManager::class.java)
            ?.unregisterNetworkCallback(callback)
    }

    private val _ui = MutableStateFlow(DockUiState())
    val ui: StateFlow<DockUiState> = _ui.asStateFlow()

    // 高频变的两块单独放，读的人只在叶子节点收集，别让主屏每秒重跑一遍
    private val _media = MutableStateFlow(MediaInfo.phoneIdle())
    val media: StateFlow<MediaInfo> = _media.asStateFlow()

    private val _voice = MutableStateFlow(VoiceState())
    val voice: StateFlow<VoiceState> = _voice.asStateFlow()

    private val _goHome = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val goHome: SharedFlow<Unit> = _goHome.asSharedFlow()

    private val _goSettings = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val goSettings: SharedFlow<Unit> = _goSettings.asSharedFlow()

    private val _screenCommands = MutableSharedFlow<ScreenCommand>(extraBufferCapacity = 8)
    val screenCommands: SharedFlow<ScreenCommand> = _screenCommands.asSharedFlow()

    private var pollJob: Job? = null
    private var miniPollJob: Job? = null
    private var hubSleepJob: Job? = null
    private var listeningJob: Job? = null
    private var disconnectedAt: Long? = null
    /** 上一次看到的 snapshot.seq；非空说明 Hub 支持长轮询。 */
    private var hubSeq: Int? = null
    private var tileLookJob: Job? = null
    private var typeLookJob: Job? = null
    private var layoutJob: Job? = null
    private var weatherCityJob: Job? = null
    private var frameJob: Job? = null
    private var companionJob: Job? = null
    private var subtitleJob: Job? = null
    private var voiceTurnId = ""
    private val conversationWindow = ConversationWindow()
    private var conversationJob: Job? = null
    private var voiceTurnAt = 0L
    private var subtitleAt = 0L
    private var deskWatch: PresenceWatch? = null

    init {
        _ui.update {
            it.copy(backgroundPageUrl = (getApplication() as? DockApp)?.backgrounds?.pageUrl)
        }
        registerNetworkWatch()
        viewModelScope.launch {
            while (isActive) {
                _media.value = phoneMedia.snapshot()
                delay(PHONE_MEDIA_MS)
            }
        }
        viewModelScope.launch {
            prefs.videoUri.collect { uri ->
                _ui.update { it.copy(backgroundVideoUri = resolveVideoUri(uri)) }
            }
        }
        viewModelScope.launch {
            val look = prefs.tileLook.first()
            _ui.update { it.copy(tileLook = look) }
        }
        viewModelScope.launch {
            val look = prefs.typeLook.first()
            _ui.update { it.copy(typeLook = look) }
        }
        viewModelScope.launch {
            prefs.powerScreen.collect { enabled ->
                _ui.update { it.copy(powerScreen = enabled) }
                if (!enabled) cancelHubSleep(wake = false)
                else noteDeskPc(_ui.value.snapshot?.pc?.online)
            }
        }
        viewModelScope.launch {
            prefs.hubSleepDelaySec.collect { sec ->
                _ui.update { it.copy(hubSleepDelaySec = sec) }
                if (!_ui.value.hubSleeping) noteDeskPc(_ui.value.snapshot?.pc?.online)
            }
        }
        viewModelScope.launch {
            prefs.hubReconnectSec.collect { sec ->
                _ui.update { it.copy(hubReconnectSec = sec) }
            }
        }
        viewModelScope.launch {
            prefs.wakeWordEnabled.collect { enabled ->
                _ui.update { it.copy(wakeWordEnabled = enabled) }
            }
        }
        viewModelScope.launch {
            prefs.deskPresenceEnabled.collect { enabled ->
                _ui.update { it.copy(deskPresenceEnabled = enabled) }
                if (!enabled) stopDeskWatch()
                else startDeskWatch()
            }
        }
        viewModelScope.launch {
            prefs.mirrorEnabled.collect { enabled ->
                _ui.update { it.copy(mirrorEnabled = enabled) }
            }
        }
        viewModelScope.launch {
            prefs.mirrorApps.collect { apps ->
                _ui.update { it.copy(mirrorAppsRaw = apps.joinToString(", ")) }
            }
        }
        viewModelScope.launch {
            prefs.voiceprintEnabled.collect { enabled ->
                _ui.update { it.copy(voiceprintEnabled = enabled) }
                VoiceprintBus.enabled = enabled
                if (!enabled) cancelVoiceprintEnroll()
            }
        }
        viewModelScope.launch {
            VoiceprintBus.enroll.collect { state ->
                _ui.update { it.copy(voiceprintEnroll = state) }
                if (state?.saved == true) loadVoiceprintSpeakers()
            }
        }
        viewModelScope.launch {
            prefs.layout.collect { layout ->
                if (!_ui.value.editing) {
                    _ui.update { it.copy(layout = layout) }
                }
            }
        }
        viewModelScope.launch {
            combine(prefs.weatherEnabled, prefs.weatherCity) { enabled, city -> enabled to city }
                .collectLatest { (enabled, city) ->
                    _ui.update {
                        it.copy(
                            weatherEnabled = enabled,
                            weatherCity = city,
                            weather = if (enabled) it.weather else null,
                            weatherError = if (enabled) it.weatherError else null,
                        )
                    }
                    if (!enabled) return@collectLatest
                    val cache = prefs.weatherCache.first()
                    if (cache != null && cacheMatches(cache, city)) {
                        _ui.update { it.copy(weather = cache, weatherError = null) }
                    } else if (_ui.value.weather?.let { info -> cacheMatches(info, city) } != true) {
                        _ui.update { it.copy(weather = null) }
                    }
                    while (isActive) {
                        val ok = refreshWeather(city)
                        delay(if (ok) WEATHER_OK_MS else WEATHER_RETRY_MS)
                    }
                }
        }
        viewModelScope.launch {
            combine(prefs.hubEnabled, prefs.connection) { enabled, connection ->
                enabled to connection
            }.collect { (enabled, connection) ->
                applyDesk(enabled, connection)
            }
        }
        viewModelScope.launch {
            prefs.miniConnection.collect { mini ->
                _ui.update { it.copy(miniConnection = mini) }
                if (_ui.value.hubEnabled) restartMiniPolling(mini)
            }
        }
    }

    fun saveDraft(host: String, portText: String, token: String) {
        val port = portText.toIntOrNull() ?: HubConnection.DEFAULT_PORT
        viewModelScope.launch {
            logConnection("SAVE_OK host=$host port=$port")
            prefs.save(HubConnection(host = host, port = port, token = token))
            // Mac 本机监控（:17891）和 Hub 跑在同一台机器上：地址直接跟随 Hub，
            // 设置页就不再单独提供一个 IP 输入框了。
            prefs.saveMini(
                MiniConnection(
                    host = host.ifBlank { MiniConnection.DEFAULT_HOST },
                    port = MiniConnection.DEFAULT_PORT,
                    // APK 里不再有默认口令：保存 Hub 时沿用手机上已有的 Mini 口令
                    token = _ui.value.miniConnection.token,
                ),
            )
        }
    }

    fun saveMiniDraft(host: String, portText: String, token: String) {
        val port = portText.toIntOrNull() ?: MiniConnection.DEFAULT_PORT
        viewModelScope.launch {
            prefs.saveMini(
                MiniConnection(
                    host = host.ifBlank { MiniConnection.DEFAULT_HOST },
                    port = port,
                    token = token.ifBlank { _ui.value.miniConnection.token },
                ),
            )
        }
    }

    fun setTileLook(look: TileLook) {
        val next = look.copy(
            opacityPercent = look.opacityPercent.coerceIn(TileLook.OPACITY_MIN, TileLook.OPACITY_MAX),
            cornerDp = look.cornerDp.coerceIn(TileLook.CORNER_MIN, TileLook.CORNER_MAX),
        )
        _ui.update { it.copy(tileLook = next) }
        tileLookJob?.cancel()
        tileLookJob = viewModelScope.launch {
            delay(160)
            prefs.saveTileLook(next)
        }
    }

    fun setTypeLook(look: TypeLook) {
        val next = look.copy(
            clockScalePercent = look.clockScalePercent.coerceIn(TypeLook.CLOCK_MIN, TypeLook.CLOCK_MAX),
            statsSize = look.statsSize.coerceIn(TypeLook.STATS_MIN, TypeLook.STATS_MAX),
            tileSize = look.tileSize.coerceIn(TypeLook.TILE_MIN, TypeLook.TILE_MAX),
            chipSize = look.chipSize.coerceIn(TypeLook.CHIP_MIN, TypeLook.CHIP_MAX),
        )
        _ui.update { it.copy(typeLook = next) }
        typeLookJob?.cancel()
        typeLookJob = viewModelScope.launch {
            delay(160)
            prefs.saveTypeLook(next)
        }
    }

    fun setPowerScreen(enabled: Boolean) {
        _ui.update { it.copy(powerScreen = enabled) }
        if (!enabled) cancelHubSleep(wake = _ui.value.hubSleeping)
        else noteDeskPc(_ui.value.snapshot?.pc?.online)
        viewModelScope.launch {
            prefs.savePowerScreen(enabled)
        }
    }

    fun setHubSleepDelay(sec: Int) {
        val next = sec.coerceIn(15, 600)
        _ui.update { it.copy(hubSleepDelaySec = next) }
        if (!_ui.value.hubSleeping) noteDeskPc(_ui.value.snapshot?.pc?.online)
        viewModelScope.launch {
            prefs.saveHubSleepDelaySec(next)
        }
    }

    fun setHubReconnect(sec: Int) {
        val next = sec.coerceIn(5, 120)
        _ui.update { it.copy(hubReconnectSec = next) }
        viewModelScope.launch {
            prefs.saveHubReconnectSec(next)
        }
    }

    fun setWeatherEnabled(enabled: Boolean) {
        _ui.update {
            it.copy(
                weatherEnabled = enabled,
                weather = if (enabled) it.weather else null,
                weatherError = if (enabled) it.weatherError else null,
            )
        }
        viewModelScope.launch {
            prefs.saveWeatherEnabled(enabled)
        }
    }

    fun setWeatherCity(city: String) {
        weatherCityJob?.cancel()
        weatherCityJob = viewModelScope.launch {
            delay(400)
            prefs.saveWeatherCity(city)
        }
    }

    fun setWakeWord(enabled: Boolean) {
        _ui.update { it.copy(wakeWordEnabled = enabled) }
        if (!enabled) {
            _voice.value = VoiceState()
            listeningJob?.cancel()
            closeConversation()
        }
        viewModelScope.launch {
            prefs.saveWakeWordEnabled(enabled)
        }
    }

    /** 在场检测（F24）总开关：默认关，开了 MainActivity 才去要摄像头权限。 */
    fun setDeskPresence(enabled: Boolean) {
        viewModelScope.launch {
            prefs.saveDeskPresenceEnabled(enabled)
        }
    }

    /** 声纹（F5）总开关：默认关。开了也只在手机本地比对，音频不出设备。 */
    fun setVoiceprintEnabled(enabled: Boolean) {
        viewModelScope.launch {
            prefs.saveVoiceprintEnabled(enabled)
        }
    }

    /** 开始给一个称呼注册声纹：接下来对着书桌说几句就行。 */
    fun startVoiceprintEnroll(raw: String) {
        val name = raw.trim().take(12)
        if (name.isEmpty()) return
        VoiceprintBus.report(
            VoiceprintBus.EnrollState(name, 0, VoiceprintBus.ENROLL_UTTERANCES),
        )
        VoiceprintBus.enrollName = name
    }

    fun cancelVoiceprintEnroll() {
        VoiceprintBus.enrollName = null
        VoiceprintBus.report(null)
    }

    fun deleteVoiceprintSpeaker(name: String) {
        viewModelScope.launch(Dispatchers.IO) {
            val store = VoiceprintStore(voiceprintFile())
            store.save(store.load().without(name))
            loadVoiceprintSpeakers()
        }
    }

    fun loadVoiceprintSpeakers() {
        viewModelScope.launch(Dispatchers.IO) {
            val names = VoiceprintStore(voiceprintFile()).load().names()
            _ui.update { it.copy(voiceprintSpeakers = names) }
        }
    }

    private fun voiceprintFile() = File(getApplication<Application>().filesDir, "voiceprint.json")

    /** 通知镜像（F35）：开关与白名单。默认关。 */
    fun setMirrorEnabled(enabled: Boolean) {
        viewModelScope.launch {
            prefs.saveMirrorEnabled(enabled)
        }
    }

    fun setMirrorApps(raw: String) {
        viewModelScope.launch {
            prefs.saveMirrorApps(raw)
        }
    }

    /** 摄像头每一帧的「有人 / 没人」从这里进来（相机分析线程）。 */
    fun onDeskFrame(person: Boolean) {
        deskWatch?.onFrame(person, SystemClock.elapsedRealtime())
    }

    /** 人自己解锁 / 说话了：算在场，但不发问候边沿（她多半正在说话）。 */
    fun noteUserInteraction() {
        deskWatch?.noteActivity(SystemClock.elapsedRealtime())
        if (_ui.value.deskSleeping) {
            _ui.update { it.copy(deskSleeping = false) }
        }
    }

    private fun startDeskWatch() {
        if (deskWatch != null) return
        deskWatch = PresenceWatch(
            onPresent = { awayMs -> viewModelScope.launch { onDeskReturned(awayMs) } },
            onAway = { viewModelScope.launch { onDeskLeft() } },
        )
    }

    private fun stopDeskWatch() {
        deskWatch = null
        if (_ui.value.deskSleeping) {
            _ui.update { it.copy(deskSleeping = false) }
        }
    }

    private fun onDeskReturned(awayMs: Long) {
        if (!_ui.value.deskPresenceEnabled) return
        _ui.update { it.copy(deskSleeping = false) }
        viewModelScope.launch(Dispatchers.IO) {
            val connection = prefs.connection.first()
            if (!currentLink().pollsHub || !connection.isConfigured) return@launch
            runCatching {
                client.reportDesk(connection, present = true, awaySec = (awayMs / 1000L).toInt())
            }
        }
    }

    private fun onDeskLeft() {
        if (!_ui.value.deskPresenceEnabled) return
        closeConversation()
        _ui.update { it.copy(deskSleeping = true) }
        viewModelScope.launch(Dispatchers.IO) {
            val connection = prefs.connection.first()
            if (!currentLink().pollsHub || !connection.isConfigured) return@launch
            runCatching { client.reportDesk(connection, present = false) }
        }
    }

    fun onWakeWord(keyword: String = "小岸", turnId: String = "") {
        noteUserInteraction()
        val interrupting = _ui.value.companionBusy ||
            !_ui.value.companionReply.isNullOrBlank() ||
            !_ui.value.companionHeard.isNullOrBlank()
        if (interrupting) {
            HelmLatency.log(turnId.ifBlank { voiceTurnId }.ifBlank { "-" }, "barge_in")
        }
        interruptCompanion()
        listeningJob?.cancel()
        rememberTurn(turnId)
        val wasSleeping = _ui.value.hubSleeping
        _voice.value = VoiceState(listening = true)
        _ui.update { it.copy(hubSleeping = false) }
        if (wasSleeping || _ui.value.powerScreen) {
            _screenCommands.tryEmit(ScreenCommand.VoiceWake)
        }
        listeningJob = viewModelScope.launch {
            delay(VOICE_FALLBACK_MS)
            val current = _voice.value
            if (current.listening && current.text.isBlank()) {
                _voice.value = current.copy(listening = false)
                if (_ui.value.powerScreen) noteDeskPc(_ui.value.snapshot?.pc?.online)
            }
        }
    }

    fun onVoiceTranscript(text: String, settled: Boolean, turnId: String = "", speaker: String = "") {
        listeningJob?.cancel()
        rememberTurn(turnId)
        _voice.value = VoiceState(listening = true, text = text, settled = settled)
        if (!settled) return
        val spoken = text.trim()
        val now = HelmLatency.now()
        if (spoken.isNotEmpty() && companionReady()) {
            conversationWindow.onInput(now)
            _voice.value = _voice.value.copy(listening = false)
            // 注册中：这几句话只喂声纹，别再当聊天发出去
            if (VoiceprintBus.enrollName == null) hearCompanion(spoken, speaker)
            return
        }
        if (spoken.isEmpty()) {
            conversationWindow.onMiss(now)
            if (!conversationWindow.listening(now)) closeConversation()
        }
        val hold = if (spoken.isEmpty()) VOICE_MISS_MS else VOICE_HOLD_MS
        listeningJob = viewModelScope.launch {
            delay(hold)
            _voice.value = _voice.value.copy(listening = false)
            if (_ui.value.powerScreen) noteDeskPc(_ui.value.snapshot?.pc?.online)
        }
    }

    private fun rememberTurn(turnId: String) {
        val id = turnId.trim()
        if (id.isEmpty()) return
        if (id == voiceTurnId) return
        voiceTurnId = id
        voiceTurnAt = HelmLatency.now()
        subtitleAt = 0L
    }

    private fun ensureTurn(): String {
        if (voiceTurnId.isBlank()) {
            voiceTurnId = HelmLatency.newTurn()
            voiceTurnAt = HelmLatency.now()
            subtitleAt = 0L
        }
        return voiceTurnId
    }

    fun setHubEnabled(enabled: Boolean) {
        viewModelScope.launch { prefs.saveHubEnabled(enabled) }
    }

    fun onHubReachable() {
        noteHubUp()
        val connection = _ui.value.connection
        if (currentLink().pollsHub && connection.isConfigured) {
            viewModelScope.launch { pullSnapshot(connection, showLoading = false) }
        }
    }

    fun enterEdit() {
        _ui.update { it.copy(editing = true) }
        _goHome.tryEmit(Unit)
    }

    fun exitEdit() {
        persistLayout(_ui.value.layout)
        _ui.update { it.copy(editing = false, selectedModule = null) }
    }

    fun selectModule(id: DockModule?) {
        _ui.update { current ->
            if (current.selectedModule == id && (id == null || current.editing)) current
            else current.copy(selectedModule = id, editing = true)
        }
    }

    fun toggleModule(id: DockModule) {
        val current = _ui.value.layout.rect(id)
        setModuleVisible(id, !(current?.visible ?: true))
    }

    fun setModuleVisible(id: DockModule, visible: Boolean) {
        val layout = _ui.value.layout
        val current = layout.rect(id)
        val next = if (current == null || current.isPlaceholder()) {
            ModuleRect(
                x = 0f,
                y = 0f,
                w = 0f,
                h = 0f,
                visible = visible,
                chrome = current?.chrome ?: true,
            )
        } else {
            current.copy(visible = visible)
        }
        commitLayout(layout.with(id, next))
    }

    fun setModuleChrome(id: DockModule, chrome: Boolean) {
        val layout = _ui.value.layout
        val current = layout.rect(id)
        val next = if (current == null || current.isPlaceholder()) {
            ModuleRect(
                x = 0f,
                y = 0f,
                w = 0f,
                h = 0f,
                visible = current?.visible ?: true,
                chrome = chrome,
            )
        } else {
            current.copy(chrome = chrome)
        }
        commitLayout(layout.with(id, next))
    }

    fun toggleModuleChrome(
        id: DockModule,
        canvasW: Float,
        canvasH: Float,
        tileW: Float,
        tileH: Float,
    ) {
        val layout = resolvedLayout(canvasW, canvasH, tileW, tileH)
        val current = layout.rect(id) ?: return
        commitLayout(layout.with(id, current.copy(chrome = !current.chrome)))
    }

    fun hideModule(id: DockModule, canvasW: Float, canvasH: Float, tileW: Float, tileH: Float) {
        val layout = resolvedLayout(canvasW, canvasH, tileW, tileH)
        val current = layout.rect(id) ?: return
        commitLayout(layout.with(id, current.copy(visible = false)))
        _ui.update { it.copy(selectedModule = null) }
    }

    fun moveModule(
        id: DockModule,
        dxPx: Float,
        dyPx: Float,
        canvasW: Float,
        canvasH: Float,
        tileW: Float,
        tileH: Float,
    ) {
        if (canvasW <= 0f || canvasH <= 0f) return
        val layout = resolvedLayout(canvasW, canvasH, tileW, tileH)
        val current = layout.rect(id) ?: return
        val next = current.nudge(dxPx / canvasW, dyPx / canvasH).snap(canvasW, canvasH)
        commitLayout(layout.with(id, next), debounce = true)
    }

    fun resizeModule(
        id: DockModule,
        dwPx: Float,
        dhPx: Float,
        canvasW: Float,
        canvasH: Float,
        tileW: Float,
        tileH: Float,
    ) {
        if (canvasW <= 0f || canvasH <= 0f) return
        val layout = resolvedLayout(canvasW, canvasH, tileW, tileH)
        val current = layout.rect(id) ?: return
        val next = current.grow(dwPx / canvasW, dhPx / canvasH).snap(canvasW, canvasH)
        commitLayout(layout.with(id, next), debounce = true)
    }

    fun resetLayout() {
        commitLayout(DockLayout())
        _ui.update { it.copy(selectedModule = null) }
    }

    fun resolvedLayout(canvasW: Float, canvasH: Float, tileW: Float, tileH: Float): DockLayout {
        val defaults = defaultDockLayout(canvasW, canvasH, tileW, tileH)
        return _ui.value.layout.mergedWith(defaults)
    }

    private fun commitLayout(layout: DockLayout, debounce: Boolean = false) {
        _ui.update { it.copy(layout = layout) }
        if (!debounce) {
            persistLayout(layout)
            return
        }
        layoutJob?.cancel()
        layoutJob = viewModelScope.launch {
            delay(180)
            persistLayout(layout)
        }
    }

    private fun persistLayout(layout: DockLayout) {
        viewModelScope.launch {
            prefs.saveLayout(layout)
        }
    }

    fun setBackgroundVideo(uri: Uri?) {
        val app = getApplication<Application>()
        val resolver = app.contentResolver
        viewModelScope.launch {
            val previous = _ui.value.backgroundVideoUri
            if (uri == null) {
                releaseContent(previous, resolver)
                prefs.saveVideoUri(VIDEO_OFF)
                return@launch
            }
            val library = (app as? DockApp)?.backgrounds?.library
            if (library == null) {
                runCatching {
                    resolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
                prefs.saveVideoUri(uri.toString())
                return@launch
            }
            val copied = runCatching {
                val rawName = displayName(resolver, uri) ?: "video.mp4"
                val name = if (BackgroundLibrary.kindOfName(rawName) == null) "$rawName.mp4" else rawName
                withContext(Dispatchers.IO) {
                    resolver.openInputStream(uri)?.use { input ->
                        library.import(name, input, BackgroundLibrary.MAX_BYTES)
                    }
                }
            }.getOrElse { error ->
                val message = (error as? BackgroundRejected)?.message ?: "无法保存视频"
                _ui.update { it.copy(settingsStatus = message, settingsOk = false) }
                return@launch
            }
            if (copied == null) {
                _ui.update { it.copy(settingsStatus = "无法读取视频", settingsOk = false) }
                return@launch
            }
            releaseContent(previous, resolver)
            prefs.saveVideoUri(Uri.fromFile(copied).toString())
            _ui.update { it.copy(settingsStatus = "已设为主屏背景", settingsOk = true) }
        }
    }

    private fun releaseContent(previous: String?, resolver: ContentResolver) {
        if (!previous.isNullOrBlank() && previous.startsWith("content:")) {
            runCatching {
                resolver.releasePersistableUriPermission(
                    Uri.parse(previous),
                    Intent.FLAG_GRANT_READ_URI_PERMISSION,
                )
            }
        }
    }

    private fun displayName(resolver: ContentResolver, uri: Uri): String? {
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
            val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (index >= 0 && cursor.moveToFirst()) {
                return cursor.getString(index)?.takeIf { it.isNotBlank() }
            }
        }
        return uri.lastPathSegment?.substringAfterLast('/')
    }

    fun testConnection(host: String, portText: String, token: String) {
        val port = portText.toIntOrNull()
        if (host.isBlank()) {
            _ui.update { it.copy(settingsStatus = "请填写 Hub 地址", settingsOk = false) }
            return
        }
        if (port == null || port !in 1..65535) {
            _ui.update { it.copy(settingsStatus = "端口无效", settingsOk = false) }
            return
        }
        if (token.isBlank()) {
            _ui.update { it.copy(settingsStatus = "请填写 Token", settingsOk = false) }
            return
        }
        val connection = HubConnection(host, port, token)
        viewModelScope.launch {
            _ui.update { it.copy(testing = true, settingsStatus = null) }
            val result = withContext(Dispatchers.IO) {
                runCatching { client.health(connection) }
            }
            result.fold(
                onSuccess = { health: Health ->
                    prefs.save(connection)
                    _ui.update {
                        it.copy(
                            testing = false,
                            settingsOk = true,
                            settingsStatus = "已连接 ${health.name}（协议 v${health.protocol}）",
                        )
                    }
                    _goHome.tryEmit(Unit)
                },
                onFailure = { error ->
                    _ui.update {
                        it.copy(
                            testing = false,
                            settingsOk = false,
                            settingsStatus = error.userMessage(),
                        )
                    }
                },
            )
        }
    }

    fun testMiniConnection(host: String, portText: String, token: String) {
        val port = portText.toIntOrNull()
        val resolvedHost = host.ifBlank { MiniConnection.DEFAULT_HOST }
        val resolvedToken = token.ifBlank { _ui.value.miniConnection.token }
        if (resolvedHost.isBlank()) {
            _ui.update { it.copy(settingsStatus = "请填写 Mini 地址", settingsOk = false) }
            return
        }
        if (port == null || port !in 1..65535) {
            _ui.update { it.copy(settingsStatus = "Mini 端口无效", settingsOk = false) }
            return
        }
        val connection = MiniConnection(resolvedHost, port, resolvedToken)
        viewModelScope.launch {
            _ui.update { it.copy(testing = true, settingsStatus = null) }
            val result = withContext(Dispatchers.IO) {
                runCatching { client.miniHealth(connection) }
            }
            result.fold(
                onSuccess = { health: Health ->
                    prefs.saveMini(connection)
                    _ui.update {
                        it.copy(
                            testing = false,
                            settingsOk = true,
                            settingsStatus = "Mini 已连接 ${health.name}（协议 v${health.protocol}）",
                        )
                    }
                },
                onFailure = { error ->
                    _ui.update {
                        it.copy(
                            testing = false,
                            settingsOk = false,
                            settingsStatus = error.userMessage().replace("Hub", "Mini"),
                        )
                    }
                },
            )
        }
    }

    fun refreshNow() {
        val connection = _ui.value.connection
        if (!currentLink().pollsHub || !connection.isConfigured) return
        viewModelScope.launch { pullSnapshot(connection, showLoading = _ui.value.snapshot == null) }
    }

    fun tapWinApp(app: WinApp) {
        if (_ui.value.preview) {
            _ui.update {
                it.copy(
                    winApps = it.winApps.map { item ->
                        if (item.id == app.id) item.copy(running = !item.running) else item
                    },
                )
            }
            return
        }
        if (!app.online || app.id in _ui.value.busyIds) return
        sendCommand(app.id) {
            client.command(_ui.value.connection, app.id, run = true)
        }
    }

    fun sendMedia(action: String) {
        if (action !in MEDIA_ACTIONS) return
        val before = _media.value
        phoneMedia.dispatch(action)
        _media.value = when (action) {
            "toggle" -> before.copy(playing = !before.playing)
            else -> before.copy(playing = true)
        }
        viewModelScope.launch {
            delay(400)
            _media.value = phoneMedia.snapshot()
        }
    }

    fun companionReady(): Boolean {
        val state = _ui.value
        return state.snapshot?.companion?.ready == true
    }

    /** 她记得的事（F28）：进设置页拉一次，删一条后原地刷新。 */
    fun loadMemory() {
        val connection = _ui.value.connection
        if (!currentLink().pollsHub || !connection.isConfigured) {
            _ui.update { it.copy(memoryItems = emptyList(), memoryError = null) }
            return
        }
        _ui.update { it.copy(memoryLoading = true, memoryError = null) }
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching { client.companionMemory(connection) }
            }
            _ui.update { state ->
                result.fold(
                    onSuccess = { list ->
                        state.copy(
                            memoryItems = list.items,
                            memoryLoading = false,
                            memoryError = null,
                        )
                    },
                    onFailure = { exc ->
                        state.copy(
                            memoryLoading = false,
                            memoryError = (exc as? HubException)?.message
                                ?: (exc.message ?: "拉不下来她记的事"),
                        )
                    },
                )
            }
        }
    }

    fun deleteMemory(key: String) {
        val connection = _ui.value.connection
        if (!currentLink().pollsHub || !connection.isConfigured) return
        _ui.update { it.copy(memoryLoading = true, memoryError = null) }
        viewModelScope.launch {
            val ok = withContext(Dispatchers.IO) {
                runCatching { client.companionMemoryDelete(connection, key) }.isSuccess
            }
            _ui.update { state ->
                if (ok) {
                    state.copy(
                        memoryItems = state.memoryItems.filterNot { it.key == key },
                        memoryLoading = false,
                    )
                } else {
                    state.copy(memoryLoading = false, memoryError = "没删掉，再试一次。")
                }
            }
        }
    }

    fun openCompanion() {
        if (!companionReady()) return
        _ui.update { it.copy(companionOpen = true, companionError = null) }
    }

    fun closeCompanion() {
        companionJob?.cancel()
        _ui.update {
            it.copy(
                companionOpen = false,
                companionBusy = false,
                companionDraft = "",
                companionError = null,
            )
        }
        holdSubtitle()
    }

    fun setCompanionDraft(text: String) {
        _ui.update { it.copy(companionDraft = text, companionError = null) }
    }

    fun hearCompanion(text: String, speaker: String = "") {
        val spoken = text.trim()
        if (spoken.isEmpty()) return
        sendCompanion(spoken, speaker)
    }

    fun sendCompanion(text: String? = null, speaker: String = "") {
        val spoken = (text ?: _ui.value.companionDraft).trim()
        if (spoken.isEmpty()) return
        // 对讲捷径（F33 正向）：「对讲，饭好了」直接从书桌喇叭出声，不走 LLM
        intercomPrefix.matchEntire(spoken)?.let { match ->
            val message = match.groupValues[1].trim()
            if (message.isNotEmpty()) {
                viewModelScope.launch { sendAnnounce(message) }
            }
            return
        }
        // 插话打断（F2）：她还在说或字幕还挂着，先把上一轮掐掉再送新的
        val interrupting = _ui.value.companionBusy || !_ui.value.companionReply.isNullOrBlank()
        companionJob?.cancel()
        companionVoice.stop()
        if (interrupting) {
            HelmLatency.log(voiceTurnId.ifBlank { "-" }, "barge_in")
            cueHubStop()
        }
        currentLink().companionBlock?.let { blocked ->
            _ui.update {
                it.copy(
                    companionOpen = false,
                    companionDraft = "",
                    companionHeard = spoken,
                    companionReply = null,
                    companionBusy = false,
                    companionError = blocked,
                )
            }
            return
        }
        val turn = ensureTurn()
        _ui.update {
            it.copy(
                companionOpen = false,
                companionDraft = "",
                companionHeard = spoken,
                companionReply = null,
                companionBusy = true,
                companionError = null,
            )
        }
        if (_ui.value.preview) {
            companionJob = viewModelScope.launch {
                delay(400)
                val preview = previewCompanionReply(spoken)
                _ui.update {
                    it.copy(
                        companionBusy = false,
                        companionReply = preview,
                    )
                }
                noteSubtitle(turn, preview.length)
                holdSubtitle()
                openConversation(preview)
            }
            return
        }
        val connection = _ui.value.connection
        if (!connection.isConfigured) {
            _ui.update { it.copy(companionBusy = false, companionError = "还没连上 Hub") }
            return
        }
        companionJob = viewModelScope.launch {
            HelmLatency.log(turn, "chat_http_start", "since_wake_ms=${HelmLatency.ms(voiceTurnAt)}")
            val started = HelmLatency.now()
            val result = withContext(Dispatchers.IO) {
                runCatching { client.companionChat(connection, spoken, turn, speaker) }
            }
            result.fold(
                onSuccess = { reply ->
                    HelmLatency.log(
                        turn,
                        "chat_http_done",
                        "ms=${HelmLatency.ms(started)} audio=${if (reply.audioId.isNullOrBlank()) 0 else 1} chars=${reply.text.length}",
                    )
                    _ui.update {
                        it.copy(companionBusy = false, companionReply = reply.text, companionError = null)
                    }
                    noteSubtitle(turn, reply.text.length)
                    holdSubtitle()
                    openConversation(reply.text)
                },
                onFailure = { error ->
                    if (error is CancellationException) throw error
                    HelmLatency.log(
                        turn,
                        "chat_http_error",
                        "ms=${HelmLatency.ms(started)} err=${error.javaClass.simpleName}",
                    )
                    _ui.update {
                        it.copy(companionBusy = false, companionError = error.userMessage())
                    }
                },
            )
        }
    }

    private fun openConversation(reply: String) {
        if (!_ui.value.wakeWordEnabled) return
        val now = HelmLatency.now()
        val waitMs = conversationWindow.onReply(reply, now)
        val until = conversationWindow.listeningUntil()
        ConversationBus.suppressUntil(now + waitMs)
        ConversationBus.openUntil(until)
        _ui.update { it.copy(conversationOpen = true) }
        conversationJob?.cancel()
        conversationJob = viewModelScope.launch {
            delay((until - HelmLatency.now()).coerceAtLeast(0L))
            if (!conversationWindow.listening(HelmLatency.now())) {
                _ui.update { it.copy(conversationOpen = false) }
            }
        }
    }

    private fun closeConversation() {
        conversationWindow.close()
        ConversationBus.close()
        conversationJob?.cancel()
        conversationJob = null
        _ui.update { it.copy(conversationOpen = false) }
    }

    private fun interruptCompanion() {
        companionJob?.cancel()
        companionJob = null
        // 协程取消停不下阻塞中的 execute()，把这条 HTTP 也掐掉
        client.cancelActiveChat()
        companionVoice.stop()
        subtitleJob?.cancel()
        cueHubStop()
        _ui.update {
            it.copy(
                companionBusy = false,
                companionOpen = false,
                companionDraft = "",
                companionHeard = null,
                companionReply = null,
                companionError = null,
            )
        }
    }

    private fun cueHubStop() {
        val connection = _ui.value.connection
        if (!currentLink().pollsHub) return
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                runCatching { client.companionStop(connection) }
            }
        }
    }

    private fun noteSubtitle(turn: String, chars: Int) {
        subtitleAt = HelmLatency.now()
        HelmLatency.log(
            turn,
            "subtitle_shown",
            "since_wake_ms=${HelmLatency.ms(voiceTurnAt)} chars=$chars",
        )
    }

    private fun holdSubtitle() {
        subtitleJob?.cancel()
        subtitleJob = viewModelScope.launch {
            delay(SUBTITLE_MS)
            _ui.update { state ->
                if (state.companionBusy) state
                else state.copy(companionHeard = null, companionReply = null, companionError = null)
            }
        }
    }

    override fun onCleared() {
        closeConversation()
        companionVoice.release()
        unregisterNetworkWatch()
        super.onCleared()
    }

    fun setPower(device: HubDevice, on: Boolean) {
        if (device.deviceType() == DeviceType.Action) return
        if (_ui.value.preview) {
            patchDevice(device.id) { it.copy(on = on) }
            return
        }
        sendCommand(device.id) { client.command(_ui.value.connection, device.id, on = on) }
    }

    fun setBrightness(device: HubDevice, brightness: Int) {
        val value = brightness.coerceIn(1, 100)
        if (_ui.value.preview) {
            patchDevice(device.id) { it.copy(on = true, brightness = value) }
            return
        }
        sendCommand(device.id) {
            client.command(
                _ui.value.connection,
                device.id,
                on = true,
                brightness = value,
            )
        }
    }

    /** 递话通知（F33/F4）：比本地已响过的 id 新的就发通知，然后记账。 */
    private fun consumeRelay(snapshot: Snapshot) {
        val notify = snapshot.notify ?: return
        if (notify.lastId <= 0) return
        viewModelScope.launch {
            val seen = prefs.lastNotifyId.first()
            val fresh = notify.items.filter { it.id > seen }
            if (fresh.isNotEmpty()) {
                RelayNotifier.consume(getApplication(), fresh)
            }
            if (notify.lastId > seen) {
                prefs.saveLastNotifyId(notify.lastId)
            }
        }
    }

    /** 通知镜像（F35）：比已亮过的 id 新的就亮一下，30 秒自己收走。 */
    private var mirrorJob: Job? = null
    private fun consumeMirror(snapshot: Snapshot) {
        val mirror = snapshot.mirror ?: return
        if (mirror.lastId <= 0) return
        viewModelScope.launch {
            val seen = prefs.lastMirrorId.first()
            if (mirror.lastId > seen) prefs.saveLastMirrorId(mirror.lastId)
            val newest = mirror.items.lastOrNull { it.id > seen } ?: return@launch
            val text = if (newest.title.isBlank()) newest.text else "${newest.title}：${newest.text}"
            _ui.update { it.copy(mirrorText = text) }
            mirrorJob?.cancel()
            mirrorJob = viewModelScope.launch {
                delay(mirrorVisibleMs)
                _ui.update { if (it.mirrorText == text) it.copy(mirrorText = null) else it }
            }
        }
    }

    /** 对讲捷径（F33 正向）：说「对讲，饭好了」——话从书桌喇叭出来，不经大脑。 */
    private val intercomPrefix = Regex("^(?:对讲|广播|喇叭说)[，,：:.\\s]+(.+)$")

    /** 通知镜像（F35）在屏上停多久：临时浮层 ≤30 秒，主屏信息层级的硬规矩。 */
    private val mirrorVisibleMs = 30_000L

    private suspend fun sendAnnounce(message: String) {
        val connection = _ui.value.connection
        if (!currentLink().pollsHub || !connection.isConfigured) return
        val result = withContext(Dispatchers.IO) {
            runCatching { client.companionAnnounce(connection, message) }
        }
        result.fold(
            onSuccess = { reply ->
                _ui.update {
                    it.copy(
                        companionHeard = message,
                        companionReply = reply.text.ifBlank { message },
                        companionError = null,
                    )
                }
            },
            onFailure = { error ->
                handleFailure(error)
                _ui.update {
                    it.copy(companionHeard = message, companionError = "对讲没送出去。")
                }
            },
        )
    }

    private fun patchDevice(deviceId: String, transform: (HubDevice) -> HubDevice) {
        _ui.update { state ->
            val snap = state.snapshot ?: return@update state
            state.copy(
                snapshot = snap.copy(
                    devices = snap.devices.map { if (it.id == deviceId) transform(it) else it },
                ),
            )
        }
    }

    private fun sendCommand(deviceId: String, block: () -> HubDevice) {
        if (deviceId in _ui.value.busyIds) return
        val connection = _ui.value.connection
        if (!currentLink().pollsHub || !connection.isConfigured) return
        viewModelScope.launch {
            _ui.update { it.copy(busyIds = it.busyIds + deviceId, banner = null) }
            val result = withContext(Dispatchers.IO) { runCatching(block) }
            result.fold(
                onSuccess = { updated ->
                    _ui.update { state ->
                        val snap = state.snapshot ?: return@update state.copy(busyIds = state.busyIds - deviceId)
                        val devices = snap.devices.map { if (it.id == updated.id) updated else it }
                        val fromSnap = devices
                            .filter { it.deviceType() == DeviceType.Action }
                            .map { it.toWinApp() }
                        state.copy(
                            busyIds = state.busyIds - deviceId,
                            stale = false,
                            snapshot = snap.copy(devices = devices),
                            winApps = fromSnap.ifEmpty { state.winApps },
                        )
                    }
                },
                onFailure = { error ->
                    handleFailure(error)
                    _ui.update { it.copy(busyIds = it.busyIds - deviceId) }
                },
            )
        }
    }

    /** 场景（F7）：点一下让 Hub 把一组设备一次做完。 */
    fun runScene(scene: Scene) {
        if (scene.id in _ui.value.sceneBusy) return
        if (_ui.value.preview) {
            _ui.update { it.copy(sceneBusy = it.sceneBusy + scene.id) }
            viewModelScope.launch {
                delay(600)
                _ui.update { it.copy(sceneBusy = it.sceneBusy - scene.id) }
            }
            return
        }
        val connection = _ui.value.connection
        if (!currentLink().pollsHub || !connection.isConfigured) return
        _ui.update { it.copy(sceneBusy = it.sceneBusy + scene.id, banner = null) }
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching { client.runScene(connection, scene.id) }
            }
            result.fold(
                onSuccess = { run ->
                    val failed = run.actions.filter { !it.ok }
                    _ui.update { state ->
                        state.copy(
                            sceneBusy = state.sceneBusy - scene.id,
                            banner = when {
                                run.ok -> null
                                failed.isNotEmpty() ->
                                    "${run.name}：${failed.first().message ?: "有一台没动成"}"
                                else -> "${run.name}没动成"
                            },
                        )
                    }
                    pullSnapshot(connection, showLoading = false)
                },
                onFailure = { error ->
                    _ui.update { it.copy(sceneBusy = it.sceneBusy - scene.id) }
                    handleFailure(error)
                },
            )
        }
    }

    private fun currentLink(): DeskLink =
        deskLink(_ui.value.hubEnabled, _ui.value.connection.isConfigured)

    private fun applyDesk(hubEnabled: Boolean, connection: HubConnection) {
        when (deskLink(hubEnabled, connection.isConfigured)) {
            DeskLink.Live -> {
                val dropPreview = _ui.value.preview || !_ui.value.hubEnabled
                _ui.update {
                    it.copy(
                        prefsReady = true,
                        hubEnabled = true,
                        connection = connection,
                        preview = false,
                        snapshot = if (dropPreview) null else it.snapshot,
                        winApps = if (dropPreview) emptyList() else it.winApps,
                        banner = null,
                    )
                }
                restartPolling(connection)
                restartMiniPolling(_ui.value.miniConnection)
            }
            DeskLink.Local -> {
                pollJob?.cancel()
                miniPollJob?.cancel()
                cancelHubSleep(wake = _ui.value.hubSleeping)
                releaseDeskSleep()
                _ui.update {
                    it.copy(
                        prefsReady = true,
                        hubEnabled = false,
                        connection = connection,
                        preview = false,
                        stale = false,
                        miniStale = false,
                        banner = null,
                        loading = false,
                        snapshot = null,
                        miniPc = null,
                        winApps = emptyList(),
                    )
                }
            }
            DeskLink.Preview -> {
                pollJob?.cancel()
                cancelHubSleep(wake = _ui.value.hubSleeping)
                releaseDeskSleep()
                _ui.update {
                    it.copy(
                        prefsReady = true,
                        hubEnabled = true,
                        connection = connection,
                        preview = true,
                        stale = false,
                        banner = null,
                        loading = false,
                        snapshot = DemoSnapshot.create(),
                        winApps = DemoSnapshot.winApps,
                    )
                }
            }
        }
    }

    private fun restartPolling(connection: HubConnection) {
        pollJob?.cancel()
        if (!connection.isConfigured) {
            _ui.update { it.copy(snapshot = null, stale = false, banner = null, loading = false) }
            return
        }
        pollJob = viewModelScope.launch {
            hubSeq = null
            pullSnapshot(connection, showLoading = _ui.value.snapshot == null)
            while (isActive) {
                val down = disconnectedAt != null
                if (down) {
                    delay(_ui.value.hubReconnectSec.coerceIn(5, 120) * 1000L)
                    val reachable = withContext(Dispatchers.IO) {
                        runCatching { client.health(connection) }.isSuccess
                    }
                    if (!reachable) continue
                    pullSnapshot(connection, showLoading = false)
                    continue
                }
                val seq = hubSeq
                if (seq == null) {
                    // 旧版 Hub 不认 since/wait：老老实实按间隔轮询
                    val hasLive = _ui.value.snapshot?.pc != null
                    delay(if (hasLive) POLL_PC_MS else POLL_MS)
                    pullSnapshot(connection, showLoading = false)
                    continue
                }
                // 长轮询：Hub 状态一变就回；一直不变就挂到 wait 超时再回来
                val started = SystemClock.elapsedRealtime()
                pullSnapshot(connection, showLoading = false, since = seq, waitSec = LONG_POLL_SEC)
                val spent = SystemClock.elapsedRealtime() - started
                if (spent < LONG_POLL_MIN_GAP_MS) {
                    // Hub 秒回（老版本把参数当空气、或 seq 抖了一下）时别打成忙等
                    delay(LONG_POLL_MIN_GAP_MS - spent)
                }
            }
        }
    }

    private fun restartMiniPolling(connection: MiniConnection) {
        miniPollJob?.cancel()
        if (!_ui.value.hubEnabled || !connection.isConfigured) {
            _ui.update { it.copy(miniPc = null, miniStale = false) }
            return
        }
        miniPollJob = viewModelScope.launch {
            pullMini(connection)
            while (isActive) {
                delay(POLL_PC_MS)
                pullMini(connection)
            }
        }
    }

    private suspend fun pullSnapshot(
        connection: HubConnection,
        showLoading: Boolean,
        since: Int? = null,
        waitSec: Int = 0,
    ) {
        if (showLoading) _ui.update { it.copy(loading = true) }
        val result = withContext(Dispatchers.IO) {
            runCatching { client.snapshot(connection, since = since, waitSec = waitSec) }
        }
        result.fold(
            onSuccess = { snapshot ->
                hubSeq = snapshot.seq
                val banner = when {
                    snapshot.hub.isLoginRequired() ->
                        snapshot.hub.message ?: "请在电脑上扫码登录米家"
                    snapshot.hub.mijia == "error" ->
                        snapshot.hub.message ?: "米家调用失败"
                    else -> null
                }
                val winApps = snapshot.devices
                    .filter { it.deviceType() == DeviceType.Action }
                    .map { it.toWinApp() }
                noteHubUp()
                noteDeskPc(snapshot.pc?.online)
                presenceReporter.onPoll(onWifi = onWifi(), reachable = true, nowMs = SystemClock.elapsedRealtime())
                consumeRelay(snapshot)
                consumeMirror(snapshot)
                // 设备名进 ASR 热词：主屏上叫什么，嘴上就该听得懂什么。
                // 词表变了语音服务会在下一句之间重装识别器，这里只管喂名字。
                AsrHotwords.setExtra(snapshot.devices.map { it.name })
                _ui.update {
                    it.copy(
                        snapshot = snapshot,
                        stale = false,
                        banner = banner,
                        loading = false,
                        winApps = winApps.ifEmpty { if (it.preview) DemoSnapshot.winApps else emptyList() },
                    )
                }
                syncFrameMode()
            },
            onFailure = { error ->
                handleFailure(error)
                presenceReporter.onPoll(
                    onWifi = onWifi(),
                    reachable = error !is HubNetworkException,
                    nowMs = SystemClock.elapsedRealtime(),
                )
                _ui.update { it.copy(loading = false, stale = it.snapshot != null) }
            },
        )
    }

    private suspend fun pullMini(connection: MiniConnection) {
        val result = withContext(Dispatchers.IO) {
            runCatching { client.miniSnapshot(connection) }
        }
        result.fold(
            onSuccess = { pc ->
                _ui.update { it.copy(miniPc = pc, miniStale = false) }
            },
            onFailure = {
                _ui.update {
                    it.copy(
                        miniStale = true,
                        miniPc = it.miniPc ?: PcStatus(online = false, updatedAt = ""),
                    )
                }
            },
        )
    }

    private fun handleFailure(error: Throwable) {
        if (error is CancellationException) throw error
        val unauthorized = error is HubException && error.isUnauthorized
        val login = error is HubException && error.isLoginRequired
        _ui.update {
            it.copy(
                banner = error.userMessage(),
                stale = it.snapshot != null && !login,
            )
        }
        if (error is HubNetworkException) {
            noteHubDown()
        } else {
            noteHubUp()
        }
        if (unauthorized) {
            _goSettings.tryEmit(Unit)
        }
    }

    private fun noteHubDown() {
        if (!currentLink().pollsHub) return
        if (disconnectedAt == null) disconnectedAt = SystemClock.elapsedRealtime()
        // Hub 抖动不熄屏：Mac/Hub 常开，是否「在工作」由 Windows 探针决定。
    }

    private fun noteHubUp() {
        disconnectedAt = null
        hubSleepJob?.cancel()
        hubSleepJob = null
        // 唤醒交给 noteDeskPc：只有 Windows 又开机了才该醒。
    }

    /** 当前默认网络走不走 Wi-Fi；没有活动网络（纯蜂窝断开）也算不在家。 */
    private fun onWifi(): Boolean {
        val manager = getApplication<Application>()
            .getSystemService(ConnectivityManager::class.java) ?: return false
        val network = manager.activeNetwork ?: return false
        val caps = manager.getNetworkCapabilities(network) ?: return false
        return caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
    }

    // ---------------- 工作判定：只看 Windows 探针的 pc.online ----------------
    // Mac / Hub 是常开的，所以「Hub 能不能连上」不能代表「人在不在工作」。
    // 你只在要干活时开 Windows 主机，因此 pc.online 才是真正的信号：
    //   pc.online == true  -> 在工作 -> 保持唤醒
    //   pc.online == false -> 主机没开 -> 等 hubSleepDelaySec 后熄屏
    private var deskOfflineAt: Long? = null
    private var deskSleepJob: Job? = null

    private fun noteDeskPc(online: Boolean?) {
        if (!currentLink().pollsHub) return
        if (online != false) {
            // PC 在线，或还没有 PC 数据 -> 保持醒着
            deskOfflineAt = null
            deskSleepJob?.cancel()
            deskSleepJob = null
            if (_ui.value.hubSleeping) {
                _ui.update { it.copy(hubSleeping = false) }
            syncFrameMode()
                if (_ui.value.powerScreen) _screenCommands.tryEmit(ScreenCommand.Wake)
            }
            return
        }
        if (!_ui.value.powerScreen) return
        if (_ui.value.hubSleeping) return
        if (deskSleepJob?.isActive == true) return
        deskOfflineAt = SystemClock.elapsedRealtime()
        val waitMs = _ui.value.hubSleepDelaySec.coerceIn(15, 600) * 1000L
        deskSleepJob = viewModelScope.launch {
            delay(waitMs)
            if (deskOfflineAt == null) return@launch
            if (!_ui.value.powerScreen) return@launch
            if (_ui.value.hubSleeping) return@launch
            closeConversation()
            _ui.update { it.copy(hubSleeping = true) }
            syncFrameMode()
            _screenCommands.tryEmit(ScreenCommand.Sleep)
        }
    }

    private fun scheduleHubSleep() {
        hubSleepJob?.cancel()
        if (!_ui.value.powerScreen) return
        if (!currentLink().pollsHub) return
        if (_ui.value.hubSleeping) return
        val started = disconnectedAt ?: return
        val waitMs = _ui.value.hubSleepDelaySec.coerceIn(15, 600) * 1000L
        val remaining = waitMs - (SystemClock.elapsedRealtime() - started)
        hubSleepJob = viewModelScope.launch {
            if (remaining > 0) delay(remaining)
            if (disconnectedAt == null) return@launch
            if (!_ui.value.powerScreen) return@launch
            closeConversation()
            _ui.update { it.copy(hubSleeping = true) }
            syncFrameMode()
            _screenCommands.tryEmit(ScreenCommand.Sleep)
        }
    }

    private fun releaseDeskSleep() {
        deskOfflineAt = null
        deskSleepJob?.cancel()
        deskSleepJob = null
    }

    private fun cancelHubSleep(wake: Boolean) {
        disconnectedAt = null
        hubSleepJob?.cancel()
        hubSleepJob = null
        val wasSleeping = _ui.value.hubSleeping
        if (wasSleeping) _ui.update { it.copy(hubSleeping = false) }
        if (wake && wasSleeping) _screenCommands.tryEmit(ScreenCommand.Wake)
    }

    // ---- F37 回忆相框 --------------------------------------------------------
    // 没数据可看的白天（电脑不在线、不在夜间压黑）把屏幕交给照片。
    // 相册没开（Hub 没有 photos/ 目录）就当没有这个功能，安静退出。

    private fun syncFrameMode() {
        val state = _ui.value
        val active = !state.hubSleeping && state.powerScreen &&
            currentLink().pollsHub && state.connection.isConfigured &&
            state.snapshot?.pc?.online == false
        if (active) {
            if (frameJob?.isActive != true) {
                frameJob = viewModelScope.launch { runFrameLoop() }
            }
            return
        }
        if (frameJob == null) return
        frameJob?.cancel()
        frameJob = null
        _ui.update { it.copy(framePhoto = null) }
    }

    private suspend fun runFrameLoop() {
        while (currentCoroutineContext().isActive) {
            // 名单跟着 snapshot 走：新照片放进 photos/ 目录，不用重启相框
            val ids = _ui.value.snapshot?.photos?.ids.orEmpty()
            if (ids.isEmpty()) {
                delay(FRAME_PHOTO_MS)
                continue
            }
            val connection = _ui.value.connection
            val id = ids.random()
            val bytes = withContext(Dispatchers.IO) {
                runCatching { client.photo(connection, id) }.getOrNull()
            }
            val bitmap = bytes?.let { decodeFramePhoto(it) }
            if (bitmap != null) {
                _ui.update { it.copy(framePhoto = bitmap.asImageBitmap()) }
            }
            delay(FRAME_PHOTO_MS)
        }
    }

    private fun decodeFramePhoto(bytes: ByteArray): android.graphics.Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        var sample = 1
        while (bounds.outWidth / sample > 2560 || bounds.outHeight / sample > 2560) sample *= 2
        val opts = BitmapFactory.Options().apply { inSampleSize = sample }
        return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts)
    }

    companion object {
        const val POLL_MS = 20_000L
        const val POLL_PC_MS = 3_000L
        const val LONG_POLL_SEC = 25
        const val LONG_POLL_MIN_GAP_MS = 800L
        const val PHONE_MEDIA_MS = 1_000L
        const val FRAME_PHOTO_MS = 45_000L
        const val WEATHER_OK_MS = 20 * 60 * 1000L
        const val WEATHER_RETRY_MS = 90 * 1000L
        const val VIDEO_OFF = "off"
        const val VIDEO_FILE_NAME = "1.mp4"
        const val VOICE_FALLBACK_MS = 18_000L
        const val VOICE_HOLD_MS = 2_200L
        const val VOICE_MISS_MS = 1_200L
        const val SUBTITLE_MS = 12_000L
        val MEDIA_ACTIONS = setOf("toggle", "next", "previous")
    }

    private fun resolveVideoUri(stored: String): String? {
        if (stored == VIDEO_OFF) return null
        if (stored.isNotBlank()) return stored
        val file = File(
            getApplication<Application>().getExternalFilesDir(null),
            VIDEO_FILE_NAME,
        )
        return if (file.isFile && file.length() > 0L) Uri.fromFile(file).toString() else null
    }

    private suspend fun refreshWeather(city: String): Boolean {
        return try {
            val info = withContext(Dispatchers.IO) {
                // 优先让 Hub 代取：手机可能连在「只有局域网、没有外网」的 Wi-Fi 上。
                // Hub 不可用（没配 / 出错）时再退回手机直连，保留原本的多源兜底。
                val connection = _ui.value.connection
                val viaHub = if (currentLink().pollsHub && connection.isConfigured) {
                    runCatching { client.weather(connection, city) }.getOrNull()
                } else {
                    null
                }
                viaHub ?: weatherClient.fetch(city)
            }
            prefs.saveWeatherCache(info)
            _ui.update { it.copy(weather = info, weatherError = null) }
            true
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            _ui.update { current ->
                current.copy(
                    weatherError = e.message ?: "天气获取失败",
                    weather = current.weather?.takeIf { cacheMatches(it, city) },
                )
            }
            false
        }
    }

    private fun cacheMatches(info: WeatherInfo, city: String): Boolean {
        val query = WeatherPlaces.normalize(city)
        return WeatherPlaces.normalize(info.query).equals(query, ignoreCase = true) ||
            WeatherPlaces.normalize(info.city).equals(query, ignoreCase = true)
    }
}

private fun previewCompanionReply(text: String): String {
    return when {
        text.contains("伞") -> "没有预报。出门前自己看一眼天。"
        text.contains("空调") -> "我还不能动手。请点旁边的开关。"
        text.contains("灯") && (text.contains("开") || text.contains("关")) ->
            "我还不能动手。灯请点旁边的开关。"
        text.contains("舒服") -> "桌上这点读数，我看见了。"
        text.contains("干什么") || text.contains("在干") -> "值班。你若开口，我就应一声。"
        else -> "嗯。我在。"
    }
}

private fun Throwable.userMessage(): String = when (this) {
    is HubException -> message
    is HubNetworkException -> message
    else -> message ?: "未知错误"
}
