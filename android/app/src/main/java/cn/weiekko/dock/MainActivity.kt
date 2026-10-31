package cn.weiekko.dock

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import cn.weiekko.dock.data.HubConnection
import cn.weiekko.dock.power.CameraPresence
import cn.weiekko.dock.power.DockPower
import cn.weiekko.dock.power.HubWatchService
import cn.weiekko.dock.power.ScreenCommand
import cn.weiekko.dock.data.ThemeSkin
import cn.weiekko.dock.ui.DockTheme
import cn.weiekko.dock.ui.DockViewModel
import cn.weiekko.dock.ui.HomeScreen
import cn.weiekko.dock.ui.SettingsScreen
import cn.weiekko.dock.voice.WakeWordService
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private val viewModel: DockViewModel by viewModels()
    private var transcriptReceiverRegistered = false
    private val requestMic = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted && viewModel.ui.value.wakeWordEnabled) {
            WakeWordService.start(this)
        } else if (!granted) {
            viewModel.setWakeWord(false)
        }
    }
    private val transcriptReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action != WakeWordService.ACTION_TRANSCRIPT) return
            viewModel.onVoiceTranscript(
                intent.getStringExtra(WakeWordService.EXTRA_TEXT).orEmpty(),
                intent.getBooleanExtra(WakeWordService.EXTRA_SETTLED, false),
                intent.getStringExtra(WakeWordService.EXTRA_TURN).orEmpty(),
                intent.getStringExtra(WakeWordService.EXTRA_SPEAKER).orEmpty(),
            )
        }
    }
    private var cameraPresence: CameraPresence? = null
    private val requestCamera = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            startDeskCamera()
        } else {
            // 不给权限就不做在场检测，开关回落到关
            viewModel.setDeskPresence(false)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            window.attributes = window.attributes.apply {
                layoutInDisplayCutoutMode =
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
        }
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
        )
        hideSystemBars()
        applyHubExtras(intent)
        handleWakeIntent(intent)
        if (!transcriptReceiverRegistered) {
            ContextCompat.registerReceiver(
                this,
                transcriptReceiver,
                IntentFilter(WakeWordService.ACTION_TRANSCRIPT),
                ContextCompat.RECEIVER_NOT_EXPORTED,
            )
            transcriptReceiverRegistered = true
        }
        lifecycleScope.launch {
            viewModel.screenCommands.collect { command ->
                when (command) {
                    ScreenCommand.Sleep -> {
                        HubWatchService.start(this@MainActivity)
                        enterHubSleep()
                    }
                    ScreenCommand.Wake -> {
                        HubWatchService.stop(this@MainActivity)
                        exitHubSleep()
                        DockPower.wake(this@MainActivity)
                    }
                    ScreenCommand.VoiceWake -> {
                        exitHubSleep()
                        DockPower.wake(this@MainActivity)
                    }
                }
            }
        }
        setContent {
            val state by viewModel.ui.collectAsStateWithLifecycle()
            val navController = rememberNavController()

            LaunchedEffect(viewModel) {
                viewModel.goSettings.collect {
                    navController.navigate("settings") {
                        launchSingleTop = true
                    }
                }
            }
            LaunchedEffect(viewModel) {
                viewModel.goHome.collect {
                    navController.navigate("home") {
                        popUpTo("settings") { inclusive = true }
                        launchSingleTop = true
                    }
                }
            }
            LaunchedEffect(state.prefsReady, state.wakeWordEnabled) {
                if (!state.prefsReady) return@LaunchedEffect
                syncWakeWord(state.wakeWordEnabled)
            }
            LaunchedEffect(state.powerScreen, state.hubSleeping, state.deskSleeping) {
                applyPowerState()
            }
            LaunchedEffect(state.prefsReady, state.deskPresenceEnabled) {
                if (!state.prefsReady) return@LaunchedEffect
                syncDeskCamera(state.deskPresenceEnabled)
            }
            LaunchedEffect(state.snapshot) {
                applyAlarmRamp()
            }

            // F36：Hub 管日期（生日/节日/考试周），手机自己看下雨天
            val skin = ThemeSkin.pick(
                state.snapshot?.theme?.occasions.orEmpty(),
                rainy = state.weather?.condition?.any { it in "雨雪雷" } == true,
            )
            DockTheme(skin = skin) {
                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    containerColor = Color.Transparent,
                    contentWindowInsets = WindowInsets(0, 0, 0, 0),
                ) {
                    if (!state.prefsReady) {
                        Box(
                            modifier = Modifier.fillMaxSize(),
                            contentAlignment = Alignment.Center,
                        ) {
                            CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
                        }
                    } else {
                        NavHost(
                            navController = navController,
                            startDestination = "home",
                            modifier = Modifier.fillMaxSize(),
                        ) {
                            composable("home") {
                                HomeScreen(
                                    state = state,
                                    media = viewModel.media,
                                    voice = viewModel.voice,
                                    onOpenSettings = { navController.navigate("settings") },
                                    onPower = viewModel::setPower,
                                    onWinClick = viewModel::tapWinApp,
                                    onMedia = viewModel::sendMedia,
                                    onSelectModule = viewModel::selectModule,
                                    onMoveModule = viewModel::moveModule,
                                    onResizeModule = viewModel::resizeModule,
                                    onToggleModuleVisible = viewModel::toggleModuleVisible,
                                    onToggleModuleChrome = viewModel::toggleModuleChrome,
                                    onExitEdit = viewModel::exitEdit,
                                    onResetLayout = viewModel::resetLayout,
                                )
                            }
                            composable("settings") {
                                SettingsScreen(
                                    state = state,
                                    canGoBack = true,
                                    onTest = viewModel::testConnection,
                                    onSave = viewModel::saveDraft,
                                    onTestMini = viewModel::testMiniConnection,
                                    onSaveMini = viewModel::saveMiniDraft,
                                    onSetVideo = viewModel::setBackgroundVideo,
                                    onSetTileLook = viewModel::setTileLook,
                                    onSetTypeLook = viewModel::setTypeLook,
                                    onSetPowerScreen = viewModel::setPowerScreen,
                                    onSetHubSleepDelay = viewModel::setHubSleepDelay,
                                    onSetHubReconnect = viewModel::setHubReconnect,
                                    onSetWeatherEnabled = viewModel::setWeatherEnabled,
                                    onSetWeatherCity = viewModel::setWeatherCity,
                                    onSetWakeWord = viewModel::setWakeWord,
                                    onSetDeskPresence = viewModel::setDeskPresence,
                                    onSetVoiceprint = viewModel::setVoiceprintEnabled,
                                    onStartVoiceprintEnroll = viewModel::startVoiceprintEnroll,
                                    onCancelVoiceprintEnroll = viewModel::cancelVoiceprintEnroll,
                                    onDeleteVoiceprintSpeaker = viewModel::deleteVoiceprintSpeaker,
                                    onLoadVoiceprint = viewModel::loadVoiceprintSpeakers,
                                    onSetMirrorEnabled = viewModel::setMirrorEnabled,
                                    onSetMirrorApps = viewModel::setMirrorApps,
                                    onLoadMemory = viewModel::loadMemory,
                                    onDeleteMemory = viewModel::deleteMemory,
                                    onSetHubEnabled = viewModel::setHubEnabled,
                                    onEditLayout = viewModel::enterEdit,
                                    onSetModuleVisible = viewModel::setModuleVisible,
                                    onSetModuleChrome = viewModel::setModuleChrome,
                                    onResetLayout = viewModel::resetLayout,
                                    onBack = { navController.popBackStack() },
                                )
                            }
                        }
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        applyHubExtras(intent)
        handleWakeIntent(intent)
    }

    override fun onDestroy() {
        if (transcriptReceiverRegistered) {
            unregisterReceiver(transcriptReceiver)
            transcriptReceiverRegistered = false
        }
        stopDeskCamera()
        super.onDestroy()
    }

    override fun onStart() {
        super.onStart()
        applyPowerState()
    }

    override fun onResume() {
        super.onResume()
        hideSystemBars()
        // 人能看见这块屏就是人到了：桌面在暗屏状态下手动解锁，摄像头不一定来得及说
        viewModel.noteUserInteraction()
        applyPowerState()
    }

    private fun applyPowerState() {
        alarmRampActive = false
        val state = viewModel.ui.value
        if (!state.powerScreen) {
            DockPower.keepScreenOn(this, false)
            return
        }
        if (state.hubSleeping || state.deskSleeping) {
            enterHubSleep()
            return
        }
        DockPower.dimForHubSleep(this, false)
        DockPower.keepScreenOn(this, true)
    }

    // F11 起床闹钟：爬坡进行中屏幕跟着日出爬亮；爬完/没在爬就回到常规电源态
    private var alarmRampActive = false

    private fun applyAlarmRamp() {
        val progress = viewModel.ui.value.snapshot?.alarm?.progress
        if (progress != null) {
            alarmRampActive = true
            DockPower.rampForAlarm(this, progress)
            return
        }
        if (alarmRampActive) {
            alarmRampActive = false
            applyPowerState()
        }
    }

    private fun enterHubSleep() {
        DockPower.dimForHubSleep(this, true)
        DockPower.keepScreenOn(this, true)
    }

    private fun exitHubSleep() {
        DockPower.dimForHubSleep(this, false)
    }

    private fun syncDeskCamera(enabled: Boolean) {
        if (!enabled) {
            stopDeskCamera()
            return
        }
        val granted = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.CAMERA,
        ) == PackageManager.PERMISSION_GRANTED
        if (granted) {
            startDeskCamera()
        } else {
            requestCamera.launch(Manifest.permission.CAMERA)
        }
    }

    private fun startDeskCamera() {
        if (cameraPresence != null) return
        val camera = CameraPresence(this, this) { person ->
            viewModel.onDeskFrame(person)
        }
        cameraPresence = camera
        camera.start()
    }

    private fun stopDeskCamera() {
        cameraPresence?.release()
        cameraPresence = null
    }

    private fun handleWakeIntent(intent: Intent?) {
        if (intent?.getBooleanExtra(EXTRA_WAKE_WORD, false) == true) {
            viewModel.onWakeWord(
                intent.getStringExtra(EXTRA_KEYWORD).orEmpty(),
                intent.getStringExtra(EXTRA_TURN).orEmpty(),
            )
            exitHubSleep()
            DockPower.wake(this)
            return
        }
        if (intent?.getBooleanExtra(EXTRA_HUB_WAKE, false) != true) return
        viewModel.onHubReachable()
        HubWatchService.stop(this)
        exitHubSleep()
        DockPower.wake(this)
    }

    private fun syncWakeWord(enabled: Boolean) {
        if (!enabled) {
            WakeWordService.stop(this)
            return
        }
        val granted = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.RECORD_AUDIO,
        ) == PackageManager.PERMISSION_GRANTED
        if (granted) {
            WakeWordService.start(this)
        } else {
            requestMic.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
    }

    private fun applyHubExtras(intent: Intent?) {
        val host = intent?.getStringExtra(EXTRA_HOST)?.trim().orEmpty()
        val token = intent?.getStringExtra(EXTRA_TOKEN)?.trim().orEmpty()
        if (host.isEmpty() || token.isEmpty()) return
        val port = intent?.getIntExtra(EXTRA_PORT, HubConnection.DEFAULT_PORT)
            ?: HubConnection.DEFAULT_PORT
        viewModel.testConnection(host, port.toString(), token)
    }

    private fun hideSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val controller = WindowInsetsControllerCompat(window, window.decorView)
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    companion object {
        const val EXTRA_HOST = "host"
        const val EXTRA_PORT = "port"
        const val EXTRA_TOKEN = "token"
        const val EXTRA_HUB_WAKE = "hub_wake"
        const val EXTRA_WAKE_WORD = "wake_word"
        const val EXTRA_KEYWORD = "keyword"
        const val EXTRA_TURN = "turn_id"

        fun wakeIntent(context: Context): Intent {
            return Intent(context, MainActivity::class.java).apply {
                addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or
                        Intent.FLAG_ACTIVITY_SINGLE_TOP or
                        Intent.FLAG_ACTIVITY_REORDER_TO_FRONT,
                )
                putExtra(EXTRA_HUB_WAKE, true)
            }
        }

        fun wakeWordIntent(context: Context, keyword: String, turnId: String = ""): Intent {
            return Intent(context, MainActivity::class.java).apply {
                addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or
                        Intent.FLAG_ACTIVITY_SINGLE_TOP or
                        Intent.FLAG_ACTIVITY_REORDER_TO_FRONT,
                )
                putExtra(EXTRA_WAKE_WORD, true)
                putExtra(EXTRA_KEYWORD, keyword)
                putExtra(EXTRA_TURN, turnId)
            }
        }
    }
}
