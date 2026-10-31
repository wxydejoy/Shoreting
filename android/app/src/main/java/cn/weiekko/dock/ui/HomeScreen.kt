package cn.weiekko.dock.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.Crossfade
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.Image
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Cloud
import androidx.compose.material.icons.outlined.Lightbulb
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material.icons.outlined.PowerSettingsNew
import androidx.compose.material.icons.outlined.Thermostat
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.PlatformTextStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import cn.weiekko.dock.R
import cn.weiekko.dock.background.BackgroundKind
import cn.weiekko.dock.background.BackgroundLibrary
import cn.weiekko.dock.data.DemoSnapshot
import cn.weiekko.dock.data.DeviceType
import cn.weiekko.dock.data.DockLayout
import cn.weiekko.dock.data.DockModule
import cn.weiekko.dock.data.HubDevice
import cn.weiekko.dock.data.MediaInfo
import cn.weiekko.dock.data.ModuleRect
import cn.weiekko.dock.data.PcStatus
import cn.weiekko.dock.data.Scene
import cn.weiekko.dock.data.TimerStatus
import cn.weiekko.dock.data.WeatherInfo
import cn.weiekko.dock.data.WinApp
import cn.weiekko.dock.data.defaultDockLayout
import cn.weiekko.dock.data.deviceType
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.min
import kotlin.math.roundToInt

private val TimeFmt = DateTimeFormatter.ofPattern("HH:mm")
private val DateFmt = DateTimeFormatter.ofPattern("M月d日 EEEE", Locale.CHINA)
@OptIn(ExperimentalFoundationApi::class)
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
    onSelectModule: (DockModule?) -> Unit = {},
    onMoveModule: (DockModule, Float, Float, Float, Float, Float, Float) -> Unit = { _, _, _, _, _, _, _ -> },
    onResizeModule: (DockModule, Float, Float, Float, Float, Float, Float) -> Unit = { _, _, _, _, _, _, _ -> },
    onHideModule: (DockModule, Float, Float, Float, Float) -> Unit = { _, _, _, _, _ -> },
    onToggleModuleChrome: (DockModule, Float, Float, Float, Float) -> Unit = { _, _, _, _, _ -> },
    onExitEdit: () -> Unit = {},
    onResetLayout: () -> Unit = {},
) {
    val colors = MaterialTheme.colorScheme
    val indoor = state.snapshot?.temperature
    val devices = state.snapshot?.devices.orEmpty()
    val pc = state.snapshot?.pc
    val videoUri = state.backgroundVideoUri
    val stillImage = videoUri != null && BackgroundLibrary.backgroundKind(videoUri) == BackgroundKind.IMAGE
    val timeShadow = if (videoUri != null) {
        Shadow(color = Color.Black.copy(alpha = 0.72f), offset = Offset.Zero, blurRadius = 22f)
    } else {
        Shadow()
    }
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
                val family = type.font.toFamily()
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
                    PlaceModule(DockModule.Clock, edit) {
                        val rect = layout.rect(DockModule.Clock)
                        val frameW = (rect?.w ?: 0.44f) * canvasW
                        val frameH = (rect?.h ?: 0.44f) * canvasH
                        val timeSp = min(frameW * 0.50f, frameH * 0.68f) *
                            (type.clockScalePercent / 100f)
                        val now = rememberNow()
                        Box(
                            modifier = Modifier
                                .fillMaxSize()
                                .combinedClickable(
                                    indication = null,
                                    interactionSource = remember { MutableInteractionSource() },
                                    onClick = {
                                        if (editing) onSelectModule(DockModule.Clock)
                                        else onOpenSettings()
                                    },
                                    onLongClick = { onSelectModule(DockModule.Clock) },
                                ),
                            contentAlignment = Alignment.Center,
                        ) {
                            Column(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    // 防烧屏：时钟每 5 分钟在 ±2px 内挪一步
                                    .offset {
                                        IntOffset(
                                            burn.clock.x.dp.toPx().roundToInt(),
                                            burn.clock.y.dp.toPx().roundToInt(),
                                        )
                                    },
                                horizontalAlignment = Alignment.CenterHorizontally,
                            ) {
                                Text(
                                    now.format(TimeFmt),
                                    fontSize = timeSp.sp,
                                    fontFamily = family,
                                    fontWeight = FontWeight.ExtraLight,
                                    letterSpacing = (-6).sp,
                                    color = colors.onBackground,
                                    lineHeight = timeSp.sp,
                                    maxLines = 1,
                                    style = TextStyle(
                                        platformStyle = PlatformTextStyle(includeFontPadding = false),
                                        shadow = timeShadow,
                                    ),
                                )
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.Center,
                                ) {
                                    Text(
                                        now.format(DateFmt),
                                        fontFamily = family,
                                        fontSize = (type.chipSize + 1).sp,
                                        fontWeight = FontWeight.Medium,
                                        color = Color.White.copy(alpha = 0.86f),
                                        maxLines = 1,
                                        style = TextStyle(shadow = timeShadow),
                                    )
                                    val weather = state.weather
                                    if (weather != null) {
                                        Text(
                                            "  ·  ",
                                            fontFamily = family,
                                            fontSize = (type.chipSize + 1).sp,
                                            fontWeight = FontWeight.Medium,
                                            color = Color.White.copy(alpha = 0.46f),
                                            style = TextStyle(shadow = timeShadow),
                                        )
                                        Text(
                                            weather.clockLine(),
                                            fontFamily = family,
                                            fontSize = (type.chipSize + 1).sp,
                                            fontWeight = FontWeight.Medium,
                                            color = Color.White.copy(alpha = 0.86f),
                                            maxLines = 1,
                                            overflow = TextOverflow.Ellipsis,
                                            modifier = Modifier.weight(1f, fill = false),
                                            style = TextStyle(shadow = timeShadow),
                                        )
                                    }
                                }
                            }
                        }
                    }

                    val showMini = state.hubEnabled && (state.miniConnection.isConfigured || state.preview)
                    val showPc = pc != null || showMini
                    if (showPc || editing) {
                        PlaceModule(DockModule.Pc, edit) {
                            if (showPc) {
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
                                    onLongPress = { onSelectModule(DockModule.Pc) },
                                    modifier = Modifier.fillMaxSize(),
                                )
                            } else {
                                EmptySlot("CPU 信息")
                            }
                        }
                    }

                    val win0 = state.winApps.getOrNull(0)
                    if (win0 != null) {
                        PlaceModule(DockModule.Win0, edit) {
                            WinTile(
                                app = win0,
                                selected = win0.running,
                                busy = win0.id in state.busyIds,
                                interactive = !editing,
                                onClick = { onWinClick(win0) },
                                onLongPress = { onSelectModule(DockModule.Win0) },
                            )
                        }
                    } else if (editing) {
                        PlaceModule(DockModule.Win0, edit) { EmptySlot("启动 1") }
                    }

                    val win1 = state.winApps.getOrNull(1)
                    if (win1 != null) {
                        PlaceModule(DockModule.Win1, edit) {
                            WinTile(
                                app = win1,
                                selected = win1.running,
                                busy = win1.id in state.busyIds,
                                interactive = !editing,
                                onClick = { onWinClick(win1) },
                                onLongPress = { onSelectModule(DockModule.Win1) },
                            )
                        }
                    } else if (editing) {
                        PlaceModule(DockModule.Win1, edit) { EmptySlot("启动 2") }
                    }

                    val win2 = state.winApps.getOrNull(2)
                    if (win2 != null) {
                        PlaceModule(DockModule.Win2, edit) {
                            WinTile(
                                app = win2,
                                selected = win2.running,
                                busy = win2.id in state.busyIds,
                                interactive = !editing,
                                onClick = { onWinClick(win2) },
                                onLongPress = { onSelectModule(DockModule.Win2) },
                            )
                        }
                    } else if (editing) {
                        PlaceModule(DockModule.Win2, edit) { EmptySlot("启动 3") }
                    }

                    val device0 = sideDevices.getOrNull(0)
                    if (device0 != null) {
                        PlaceModule(DockModule.Device0, edit) {
                            DeviceTile(
                                device = device0,
                                busy = device0.id in state.busyIds,
                                interactive = !editing,
                                onToggle = { onPower(device0, !device0.on) },
                                onLongPress = { onSelectModule(DockModule.Device0) },
                            )
                        }
                    } else if (editing) {
                        PlaceModule(DockModule.Device0, edit) { EmptySlot("设备 1") }
                    }

                    val device1 = sideDevices.getOrNull(1)
                    if (device1 != null) {
                        PlaceModule(DockModule.Device1, edit) {
                            DeviceTile(
                                device = device1,
                                busy = device1.id in state.busyIds,
                                interactive = !editing,
                                onToggle = { onPower(device1, !device1.on) },
                                onLongPress = { onSelectModule(DockModule.Device1) },
                            )
                        }
                    } else if (editing) {
                        PlaceModule(DockModule.Device1, edit) { EmptySlot("设备 2") }
                    }

                    PlaceModule(DockModule.Media, edit) {
                        LiveMediaTile(
                            media = media,
                            interactive = !editing,
                            onAction = onMedia,
                            onLongPress = { onSelectModule(DockModule.Media) },
                        )
                    }

                    val weather = state.weather
                    if (weather != null) {
                        PlaceModule(DockModule.Weather, edit) {
                            WeatherChip(
                                weather = weather,
                                interactive = !editing,
                                onLongPress = { onSelectModule(DockModule.Weather) },
                            )
                        }
                    } else if (editing) {
                        PlaceModule(DockModule.Weather, edit) { EmptySlot("室外天气") }
                    }

                    if (indoor != null) {
                        PlaceModule(DockModule.Indoor, edit) {
                            IndoorChip(
                                celsius = indoor.celsius,
                                humidity = indoor.humidity,
                                name = indoor.name,
                                online = indoor.online && !state.stale,
                                interactive = !editing,
                                onLongPress = { onSelectModule(DockModule.Indoor) },
                            )
                        }
                    } else if (editing) {
                        PlaceModule(DockModule.Indoor, edit) { EmptySlot("室内温湿度") }
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
                        overflow = TextOverflow.Ellipsis,
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

            if (burn.dim < 1f) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color.Black.copy(alpha = 1f - burn.dim))
                        .pointerInput(Unit) {},
                )
            }

            val captionText = when {
                state.hubSleeping || voiceListening -> null
                state.companionBusy -> "……"
                !state.companionError.isNullOrBlank() -> state.companionError
                !state.companionReply.isNullOrBlank() -> state.companionReply
                else -> null
            }
            if (captionText != null) {
                CompanionCaption(
                    text = captionText,
                    modifier = Modifier
                        .align(Alignment.BottomStart)
                        .fillMaxWidth(0.42f)
                        .padding(start = 20.dp, bottom = 18.dp)
                        .zIndex(17f),
                )
            }

            // 通知镜像（F35）：手机收到的重要消息在顶部亮一下，30 秒自己收走。
            // 睡着了就别亮了——黑屏是让人休息的，不是让人看消息的。
            val mirrorLine = state.mirrorText
            if (mirrorLine != null && !state.hubSleeping && !state.editing) {
                MirrorBanner(
                    text = mirrorLine,
                    modifier = Modifier
                        .align(Alignment.TopCenter)
                        .padding(top = 10.dp)
                        .zIndex(16f),
                )
            }

            // 访客模式（F26）：开着的时候她自己知道收着点，屏上留个角标提醒主人
            if (state.snapshot?.guest != null && !state.hubSleeping && !state.editing) {
                GuestBadge(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(top = 10.dp, end = 14.dp)
                        .zIndex(16f),
                )
            }

            if (state.hubSleeping && !voiceListening) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color.Black)
                        .zIndex(20f)
                        .pointerInput(Unit) {},
                )
            }

            // 免唤醒连续对话：窗口开着时屏幕边缘一圈很淡的呼吸光——「我在听」
            if (state.conversationOpen && !state.hubSleeping && !state.editing) {
                val converse = rememberInfiniteTransition(label = "converse")
                val glow = converse.animateFloat(
                    initialValue = 0.08f,
                    targetValue = 0.26f,
                    animationSpec = infiniteRepeatable(
                        animation = tween(1600),
                        repeatMode = RepeatMode.Reverse,
                    ),
                    label = "glow",
                )
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .border(3.dp, MaterialTheme.colorScheme.primary.copy(alpha = glow.value))
                        .pointerInput(Unit) {},
                )
            }

            if (voiceListening) {
                LiveWakeListeningChip(
                    voice = voice,
                    modifier = Modifier
                        .align(Alignment.BottomStart)
                        .padding(start = 20.dp, bottom = 16.dp, end = 80.dp)
                        .zIndex(30f),
                )
            }
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
