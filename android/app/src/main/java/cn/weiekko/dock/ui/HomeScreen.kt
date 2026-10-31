package cn.weiekko.dock.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.Crossfade
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.zIndex
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import cn.weiekko.dock.background.BackgroundKind
import cn.weiekko.dock.background.BackgroundLibrary
import cn.weiekko.dock.data.DemoSnapshot
import cn.weiekko.dock.data.DeviceType
import cn.weiekko.dock.data.HubDevice
import cn.weiekko.dock.data.MediaInfo
import cn.weiekko.dock.data.ModuleCatalog
import cn.weiekko.dock.data.ModuleType
import cn.weiekko.dock.data.Scene
import cn.weiekko.dock.data.WinApp
import cn.weiekko.dock.data.defaultDockLayout
import cn.weiekko.dock.data.deviceType
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlin.math.min
import kotlin.math.roundToInt

@Composable
fun HomeScreen(
    state: DockUiState,
    media: StateFlow<MediaInfo>,
    voice: StateFlow<VoiceState>,
    onOpenSettings: () -> Unit,
    onPower: (HubDevice, Boolean) -> Unit,
    onWinClick: (WinApp) -> Unit,
    onMedia: (String) -> Unit,
    onScene: (Scene) -> Unit = {},
    onSelectModule: (String?) -> Unit = {},
    onMoveModule: (String, Float, Float, Float, Float, Float, Float) -> Unit = { _, _, _, _, _, _, _ -> },
    onResizeModule: (String, Float, Float, Float, Float, Float, Float) -> Unit = { _, _, _, _, _, _, _ -> },
    onHideModule: (String, Float, Float, Float, Float) -> Unit = { _, _, _, _, _ -> },
    onToggleModuleChrome: (String, Float, Float, Float, Float) -> Unit = { _, _, _, _, _ -> },
    onExitEdit: () -> Unit = {},
    onResetLayout: () -> Unit = {},
) {
    val colors = MaterialTheme.colorScheme
    val indoor = state.snapshot?.temperature
    val devices = state.snapshot?.devices.orEmpty()
    val pc = state.snapshot?.pc
    val videoUri = state.backgroundVideoUri
    val stillImage = videoUri != null && BackgroundLibrary.backgroundKind(videoUri) == BackgroundKind.IMAGE
    val timeShadow =
        if (videoUri != null) Shadow(Color.Black.copy(alpha = 0.72f), androidx.compose.ui.geometry.Offset.Zero, 22f)
        else Shadow()
    val editing = state.editing
    // 只订阅「有没有在听」：ASR 的半句交给下面的叶子节点自己收，
    // 否则一句话里十几二十次 partial 会把整个主屏重新组合一遍。
    val voiceListening by remember(voice) {
        voice.map { it.listening }.distinctUntilChanged()
    }.collectAsStateWithLifecycle(initialValue = false)
    BackHandler(enabled = editing, onBack = onExitEdit)

    // F37 回忆相框：没有数据可看的时刻，整块屏幕交给一张慢慢换的照片
    val frameBitmap = state.framePhoto
    if (frameBitmap != null) {
        Crossfade(targetState = frameBitmap, label = "frame") { bitmap ->
            Image(
                bitmap = bitmap,
                contentDescription = null,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
                alpha = 0.92f,
            )
        }
        return
    }
    val burn = rememberBurnShifts(active = !editing)

    CompositionLocalProvider(
        LocalTileLook provides state.tileLook,
        LocalTypeLook provides state.typeLook,
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(if (videoUri == null) colors.background else Color.Transparent),
        ) {
            if (videoUri != null) {
                if (stillImage) {
                    ImageBackground(uri = videoUri)
                } else {
                    VideoBackground(uri = videoUri, playing = !state.hubSleeping)
                }
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color.Black.copy(alpha = 0.36f)),
                )
            }

            // F39 一句话壁纸：壁纸的一部分，不是数据——淡淡的，贴着底边，
            // 编辑态收起来，别挡拖拽；hubSleeping 时屏幕本来就很暗，无所谓
            val wallpaperLine = state.snapshot?.wallpaper?.text
            if (!editing && !wallpaperLine.isNullOrBlank()) {
                Text(
                    text = wallpaperLine,
                    style = MaterialTheme.typography.bodyMedium,
                    color = colors.onBackground.copy(alpha = 0.42f),
                    maxLines = 1,
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(bottom = 6.dp),
                )
            }

            BoxWithConstraints(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(horizontal = 20.dp, vertical = 10.dp),
            ) {
                val type = state.typeLook
                val canvasW = maxWidth.value
                val canvasH = maxHeight.value
                val tileH = min(canvasH * 0.22f, 84f)
                val tileW = min(canvasW * 0.20f, tileH * 1.7f)
                val density = LocalDensity.current
                val layout = remember(state.layout, canvasW, canvasH, tileW, tileH) {
                    state.layout.mergedWith(defaultDockLayout(canvasW, canvasH, tileW, tileH))
                }
                val sideDevices = devices.filter {
                    val kind = it.deviceType()
                    kind == DeviceType.Light || kind == DeviceType.Switch
                }.take(2)
                val showMini = state.hubEnabled && (state.miniConnection.isConfigured || state.preview)
                val showPc = pc != null || showMini

                val edit = EditCanvas(
                    layout = layout,
                    canvasW = canvasW,
                    canvasH = canvasH,
                    tileW = tileW,
                    tileH = tileH,
                    editing = editing,
                    selected = state.selectedModule,
                    density = density,
                    onSelect = onSelectModule,
                    onMove = onMoveModule,
                    onResize = onResizeModule,
                    onHide = onHideModule,
                    onToggleChrome = onToggleModuleChrome,
                )

                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        // 防烧屏：画面静止 30 分钟后整个模块层整体偏移
                        .offset {
                            IntOffset(
                                burn.canvas.x.dp.toPx().roundToInt(),
                                burn.canvas.y.dp.toPx().roundToInt(),
                            )
                        }
                        .then(
                            if (editing) {
                                Modifier.pointerInput(Unit) {
                                    detectTapGestures { onSelectModule(null) }
                                }
                            } else {
                                Modifier
                            },
                        ),
                ) {
                    // 磁贴画法：目录里登记了什么就摆什么，每个种类认领自己的一支。
                    ModuleCatalog.all.forEach { slot ->
                        PlaceModule(slot, edit) {
                            when (slot.type) {
                                ModuleType.Clock -> {
                                    val rect = edit.layout.rect(slot.id)
                                    ClockModule(
                                        frameW = (rect?.w ?: 0.44f) * canvasW,
                                        frameH = (rect?.h ?: 0.44f) * canvasH,
                                        clockScalePercent = type.clockScalePercent,
                                        drift = burn.clock,
                                        timeShadow = timeShadow,
                                        weatherLine = state.weather?.clockLine(),
                                        editing = editing,
                                        onSelect = { onSelectModule(slot.id) },
                                        onOpenSettings = onOpenSettings,
                                    )
                                }

                                ModuleType.Pc -> if (showPc) {
                                    PcMonitorRow(
                                        pc = pc,
                                        stale = state.stale || pc?.online != true,
                                        mini = when {
                                            !state.hubEnabled -> null
                                            state.miniConnection.isConfigured -> state.miniPc
                                            state.preview -> DemoSnapshot.mini
                                            else -> null
                                        },
                                        miniStale = state.miniStale || (state.miniPc?.online != true && state.miniConnection.isConfigured),
                                        showMini = showMini,
                                        interactive = !editing,
                                        onClick = {},
                                        onLongPress = { onSelectModule(slot.id) },
                                        modifier = Modifier.fillMaxSize(),
                                    )
                                } else if (editing) {
                                    EmptySlot(slot.label)
                                }

                                ModuleType.Win -> {
                                    val app = state.winApps.getOrNull(slot.index)
                                    if (app != null) {
                                        WinTile(
                                            app = app,
                                            selected = app.running,
                                            busy = app.id in state.busyIds,
                                            interactive = !editing,
                                            onClick = { onWinClick(app) },
                                            onLongPress = { onSelectModule(slot.id) },
                                        )
                                    } else if (editing) {
                                        EmptySlot(slot.label)
                                    }
                                }

                                ModuleType.Device -> {
                                    val device = sideDevices.getOrNull(slot.index)
                                    if (device != null) {
                                        DeviceTile(
                                            device = device,
                                            busy = device.id in state.busyIds,
                                            interactive = !editing,
                                            onToggle = { onPower(device, !device.on) },
                                            onLongPress = { onSelectModule(slot.id) },
                                        )
                                    } else if (editing) {
                                        EmptySlot(slot.label)
                                    }
                                }

                                ModuleType.Media -> LiveMediaTile(
                                    media = media,
                                    interactive = !editing,
                                    onAction = onMedia,
                                    onLongPress = { onSelectModule(slot.id) },
                                )

                                ModuleType.Weather -> {
                                    val weather = state.weather
                                    if (weather != null) {
                                        WeatherChip(
                                            weather = weather,
                                            interactive = !editing,
                                            onLongPress = { onSelectModule(slot.id) },
                                        )
                                    } else if (editing) {
                                        EmptySlot(slot.label)
                                    }
                                }

                                ModuleType.Indoor -> {
                                    if (indoor != null) {
                                        IndoorChip(
                                            celsius = indoor.celsius,
                                            humidity = indoor.humidity,
                                            name = indoor.name,
                                            online = indoor.online && !state.stale,
                                            interactive = !editing,
                                            onLongPress = { onSelectModule(slot.id) },
                                        )
                                    } else if (editing) {
                                        EmptySlot(slot.label)
                                    }
                                }
                            }
                        }
                    }
                }

                if (state.preview && !editing) {
                    Text(
                        "预览",
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier
                            .align(Alignment.TopEnd)
                            .padding(top = 14.dp, end = 4.dp),
                    )
                }

                val companionTimer = if (editing) null else state.snapshot?.companion?.timer
                if (companionTimer != null) {
                    TimerRing(
                        timer = companionTimer,
                        modifier = Modifier
                            .align(Alignment.TopEnd)
                            .padding(top = 14.dp, end = 16.dp)
                            .zIndex(15f),
                    )
                }

                state.banner?.let { banner ->
                    Text(
                        banner,
                        style = MaterialTheme.typography.labelMedium,
                        color = colors.error,
                        maxLines = 1,
                        overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                        modifier = Modifier
                            .align(Alignment.TopCenter)
                            .padding(top = 8.dp)
                            .padding(horizontal = 48.dp)
                            .zIndex(8f),
                    )
                }

                // 场景（F7）：右下角一列按钮，点一下让 Hub 把一组设备一次做完。
                // 编辑态收起别挡拖拽；睡着了屏幕本来就黑，不画。
                val scenes = state.snapshot?.scenes.orEmpty()
                if (scenes.isNotEmpty() && !editing && !state.hubSleeping) {
                    SceneDock(
                        scenes = scenes,
                        busy = state.sceneBusy,
                        onRun = onScene,
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(end = 20.dp, bottom = 32.dp)
                            .zIndex(9f),
                    )
                }

                if (editing) {
                    EditBar(
                        modifier = Modifier
                            .align(Alignment.TopStart)
                            .padding(top = 2.dp)
                            .zIndex(12f),
                        onDone = onExitEdit,
                        onReset = onResetLayout,
                    )
                }
            }

            ScreenOverlays(
                state = state,
                voice = voice,
                voiceListening = voiceListening,
                dim = burn.dim,
                editing = editing,
            )
        }
    }
}

@Preview(showBackground = true, backgroundColor = 0xFF0E0E0E, widthDp = 914, heightDp = 411)
@Composable
private fun HomePreview() {
    DockTheme {
        HomeScreen(
            state = DockUiState(
                prefsReady = true,
                preview = true,
                snapshot = DemoSnapshot.create(),
                weather = DemoSnapshot.weather,
            ),
            media = remember { MutableStateFlow(DemoSnapshot.media) },
            voice = remember { MutableStateFlow(VoiceState()) },
            onOpenSettings = {},
            onPower = { _, _ -> },
            onWinClick = {},
            onMedia = {},
        )
    }
}
