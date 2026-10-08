package cn.weiekko.dock.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.PlatformTextStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import cn.weiekko.dock.R
import cn.weiekko.dock.data.DemoSnapshot
import cn.weiekko.dock.data.DeviceType
import cn.weiekko.dock.data.DockLayout
import cn.weiekko.dock.data.DockModule
import cn.weiekko.dock.data.HubDevice
import cn.weiekko.dock.data.MediaInfo
import cn.weiekko.dock.data.ModuleRect
import cn.weiekko.dock.data.PcStatus
import cn.weiekko.dock.data.WeatherInfo
import cn.weiekko.dock.data.WinApp
import cn.weiekko.dock.data.defaultDockLayout
import cn.weiekko.dock.data.deviceType
import kotlinx.coroutines.delay
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.min
import kotlin.math.roundToInt

private val TimeFmt = DateTimeFormatter.ofPattern("HH:mm")
private val DateFmt = DateTimeFormatter.ofPattern("M月d日 EEEE", Locale.CHINA)
private val StatShadow = Shadow(color = Color.Black.copy(alpha = 0.95f), offset = Offset(0f, 2f), blurRadius = 22f)

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun HomeScreen(
    state: DockUiState,
    onOpenSettings: () -> Unit,
    onPower: (HubDevice, Boolean) -> Unit,
    onWinClick: (WinApp) -> Unit,
    onMedia: (String) -> Unit,
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
    val timeShadow = if (videoUri != null) {
        Shadow(color = Color.Black.copy(alpha = 0.72f), offset = Offset.Zero, blurRadius = 22f)
    } else {
        Shadow()
    }
    val editing = state.editing
    BackHandler(enabled = editing, onBack = onExitEdit)

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
                VideoBackground(uri = videoUri, playing = !state.hubSleeping)
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color.Black.copy(alpha = 0.36f)),
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
                                modifier = Modifier.fillMaxWidth(),
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
                        MediaTile(
                            media = state.media,
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

            val captionText = when {
                state.hubSleeping || state.voiceListening -> null
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

            if (state.hubSleeping && !state.voiceListening) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color.Black)
                        .zIndex(20f)
                        .pointerInput(Unit) {},
                )
            }

            if (state.voiceListening) {
                WakeListeningChip(
                    text = state.voiceText,
                    settled = state.voiceSettled,
                    modifier = Modifier
                        .align(Alignment.BottomStart)
                        .padding(start = 20.dp, bottom = 16.dp, end = 80.dp)
                        .zIndex(30f),
                )
            }
        }
    }
}

private data class EditCanvas(
    val layout: DockLayout,
    val canvasW: Float,
    val canvasH: Float,
    val tileW: Float,
    val tileH: Float,
    val editing: Boolean,
    val selected: DockModule?,
    val density: Density,
    val onSelect: (DockModule?) -> Unit,
    val onMove: (DockModule, Float, Float, Float, Float, Float, Float) -> Unit,
    val onResize: (DockModule, Float, Float, Float, Float, Float, Float) -> Unit,
    val onHide: (DockModule, Float, Float, Float, Float) -> Unit,
    val onToggleChrome: (DockModule, Float, Float, Float, Float) -> Unit,
)

@Composable
private fun PlaceModule(
    id: DockModule,
    canvas: EditCanvas,
    content: @Composable BoxScope.() -> Unit,
) {
    val rect = canvas.layout.rect(id) ?: return
    val density = canvas.density
    ModuleBox(
        rect = rect,
        canvasW = canvas.canvasW,
        canvasH = canvas.canvasH,
        editing = canvas.editing,
        selected = canvas.selected == id,
        onSelect = { canvas.onSelect(id) },
        onMove = { dx, dy ->
            canvas.onMove(
                id,
                with(density) { dx.toDp().value },
                with(density) { dy.toDp().value },
                canvas.canvasW,
                canvas.canvasH,
                canvas.tileW,
                canvas.tileH,
            )
        },
        onResize = { dw, dh ->
            canvas.onResize(
                id,
                with(density) { dw.toDp().value },
                with(density) { dh.toDp().value },
                canvas.canvasW,
                canvas.canvasH,
                canvas.tileW,
                canvas.tileH,
            )
        },
        onHide = {
            canvas.onHide(id, canvas.canvasW, canvas.canvasH, canvas.tileW, canvas.tileH)
        },
        onToggleChrome = {
            canvas.onToggleChrome(id, canvas.canvasW, canvas.canvasH, canvas.tileW, canvas.tileH)
        },
        content = content,
    )
}

@Composable
private fun ModuleBox(
    rect: ModuleRect,
    canvasW: Float,
    canvasH: Float,
    editing: Boolean,
    selected: Boolean,
    onSelect: () -> Unit,
    onMove: (Float, Float) -> Unit,
    onResize: (Float, Float) -> Unit,
    onHide: () -> Unit,
    onToggleChrome: () -> Unit,
    content: @Composable BoxScope.() -> Unit,
) {
    if (!rect.visible && !editing) return
    val density = LocalDensity.current
    val shape = RoundedCornerShape(10.dp)
    var dx by remember { mutableFloatStateOf(0f) }
    var dy by remember { mutableFloatStateOf(0f) }
    var dw by remember { mutableFloatStateOf(0f) }
    var dh by remember { mutableFloatStateOf(0f) }
    val extraW = with(density) { dw.toDp().value }
    val extraH = with(density) { dh.toDp().value }

    Box(
        modifier = Modifier
            .zIndex(if (selected && editing) 6f else 1f)
            .offset {
                IntOffset(
                    x = ((rect.x * canvasW).dp.toPx() + dx).roundToInt(),
                    y = ((rect.y * canvasH).dp.toPx() + dy).roundToInt(),
                )
            }
            .width((rect.w * canvasW + extraW).coerceAtLeast(48f).dp)
            .height((rect.h * canvasH + extraH).coerceAtLeast(40f).dp)
            .alpha(if (rect.visible) 1f else 0.38f),
    ) {
        CompositionLocalProvider(LocalTileChrome provides rect.chrome) {
            Box(modifier = Modifier.fillMaxSize(), content = content)
        }
        if (editing) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .border(
                        width = if (selected) 2.dp else 1.dp,
                        color = if (selected) Color.White.copy(alpha = 0.92f) else Color.White.copy(alpha = 0.32f),
                        shape = shape,
                    )
                    .pointerInput(Unit) {
                        detectDragGestures(
                            onDragStart = { onSelect() },
                            onDragEnd = {
                                val x = dx
                                val y = dy
                                if (x != 0f || y != 0f) {
                                    onMove(x, y)
                                    dx = 0f
                                    dy = 0f
                                }
                            },
                            onDragCancel = {
                                val x = dx
                                val y = dy
                                if (x != 0f || y != 0f) {
                                    onMove(x, y)
                                    dx = 0f
                                    dy = 0f
                                }
                            },
                            onDrag = { change, amount ->
                                change.consume()
                                dx += amount.x
                                dy += amount.y
                            },
                        )
                    },
            )
            if (selected) {
                Row(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(4.dp),
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    EditChip(
                        label = if (rect.chrome) "去底" else "加底",
                        onClick = onToggleChrome,
                    )
                    EditChip(label = "隐藏", onClick = onHide)
                }
                Box(
                    modifier = Modifier
                        .align(Alignment.BottomEnd)
                        .padding(2.dp)
                        .size(28.dp)
                        .background(Color.Black.copy(alpha = 0.45f), CircleShape)
                        .pointerInput(Unit) {
                            detectDragGestures(
                                onDragEnd = {
                                    val x = dw
                                    val y = dh
                                    if (x != 0f || y != 0f) {
                                        onResize(x, y)
                                        dw = 0f
                                        dh = 0f
                                    }
                                },
                                onDragCancel = {
                                    val x = dw
                                    val y = dh
                                    if (x != 0f || y != 0f) {
                                        onResize(x, y)
                                        dw = 0f
                                        dh = 0f
                                    }
                                },
                                onDrag = { change, amount ->
                                    change.consume()
                                    dw += amount.x
                                    dh += amount.y
                                },
                            )
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        painter = painterResource(R.drawable.ri_expand),
                        contentDescription = "缩放",
                        tint = Color.White,
                        modifier = Modifier
                            .size(18.dp)
                            .rotate(90f),
                    )
                }
            }
        }
    }
}

@Composable
private fun EditBar(
    modifier: Modifier = Modifier,
    onDone: () -> Unit,
    onReset: () -> Unit,
) {
    Surface(
        modifier = modifier,
        color = Color.Black.copy(alpha = 0.58f),
        shape = RoundedCornerShape(14.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 4.dp),
        ) {
            TextButton(onClick = onDone) {
                Text("完成", color = Color.White, fontWeight = FontWeight.SemiBold)
            }
            TextButton(onClick = onReset) {
                Text("恢复默认", color = Color.White.copy(alpha = 0.88f))
            }
            Text(
                "拖动移动 · 角点缩放 · 去底",
                color = Color.White.copy(alpha = 0.62f),
                fontSize = 11.sp,
                modifier = Modifier.padding(end = 12.dp),
            )
        }
    }
}

@Composable
private fun EditChip(
    label: String,
    onClick: () -> Unit,
) {
    Surface(
        modifier = Modifier.clickable(
            indication = null,
            interactionSource = remember { MutableInteractionSource() },
            onClick = onClick,
        ),
        color = Color.Black.copy(alpha = 0.62f),
        shape = RoundedCornerShape(8.dp),
    ) {
        Text(
            label,
            color = Color.White,
            fontSize = 11.sp,
            fontWeight = FontWeight.Medium,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
        )
    }
}

@Composable
private fun EmptySlot(label: String) {
    TilePanel(modifier = Modifier.fillMaxSize().alpha(0.55f)) {
        Text(
            label,
            color = Color.White.copy(alpha = 0.78f),
            modifier = Modifier.align(Alignment.Center),
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
private fun rememberNow(): LocalDateTime {
    var now by remember { mutableStateOf(LocalDateTime.now()) }
    LaunchedEffect(Unit) {
        while (true) {
            now = LocalDateTime.now()
            delay(1_000)
        }
    }
    return now
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun PcMonitorRow(
    pc: PcStatus?,
    stale: Boolean,
    onClick: () -> Unit,
    onLongPress: () -> Unit = {},
    interactive: Boolean = true,
    mini: PcStatus? = null,
    miniStale: Boolean = false,
    showMini: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val type = LocalTypeLook.current
    val family = type.font.toFamily()
    val size = type.statsSize
    val labelSize = (size * 0.72f).coerceAtLeast(10f)
    val columns = pcStatColumns(pc, mini.takeIf { showMini }, includeFps = true)
    TilePanel(
        modifier = modifier
            .alpha(if (!showMini && stale) 0.78f else 1f)
            .combinedClickable(
                enabled = interactive,
                indication = null,
                interactionSource = remember { MutableInteractionSource() },
                role = Role.Button,
                onClick = onClick,
                onLongClick = onLongPress,
            ),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 12.dp, vertical = 6.dp),
            verticalArrangement = Arrangement.SpaceEvenly,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            PcStatRow(
                columns = columns.map { it.copy(value = it.top, sub = it.topSub) },
                showLabels = true,
                family = family,
                size = size,
                labelSize = labelSize,
                dimmed = showMini && stale,
            )
            if (showMini) {
                PcStatRow(
                    columns = columns.map { it.copy(value = it.bottom, sub = it.bottomSub) },
                    showLabels = false,
                    family = family,
                    size = size,
                    labelSize = labelSize,
                    dimmed = miniStale,
                )
            }
        }
    }
}

private data class PcStatColumn(
    val label: String,
    val top: String,
    val topSub: String? = null,
    val bottom: String = "",
    val bottomSub: String? = null,
    val value: String = top,
    val sub: String? = topSub,
)

private fun pcStatColumns(pc: PcStatus?, mini: PcStatus?, includeFps: Boolean): List<PcStatColumn> {
    val cpu = pc?.cpu
    val miniCpu = mini?.cpu
    val gpu = pc?.gpu
    val miniGpu = mini?.gpu
    val columns = mutableListOf(
        PcStatColumn(
            label = "CPU",
            top = cpu?.percent?.let(::formatPercent) ?: "--",
            topSub = cpu?.tempCelsius?.let(::formatTemp),
            bottom = miniCpu?.percent?.let(::formatPercent) ?: "--",
            bottomSub = miniCpu?.tempCelsius?.let(::formatTemp),
        ),
        PcStatColumn(
            label = "MEM",
            top = pc?.memory?.percent?.let(::formatPercent) ?: "--",
            bottom = mini?.memory?.percent?.let(::formatPercent) ?: "--",
        ),
        PcStatColumn(
            label = "GPU",
            top = gpu?.percent?.let(::formatPercent) ?: "--",
            topSub = gpu?.tempCelsius?.let(::formatTemp) ?: gpu?.name?.take(10),
            bottom = miniGpu?.percent?.let(::formatPercent) ?: "--",
            bottomSub = miniGpu?.tempCelsius?.let(::formatTemp) ?: miniGpu?.name?.take(10),
        ),
    )
    if (includeFps && pc?.fps != null) {
        columns += PcStatColumn(
            label = "FPS",
            top = pc.fps.roundToInt().toString(),
            bottom = "",
        )
    }
    return columns
}

@Composable
private fun PcStatRow(
    columns: List<PcStatColumn>,
    showLabels: Boolean,
    family: FontFamily,
    size: Int,
    labelSize: Float,
    dimmed: Boolean,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .alpha(if (dimmed) 0.72f else 1f),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        columns.forEach { item ->
            Column(
                modifier = Modifier.weight(1f),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                if (showLabels) {
                    Text(
                        item.label,
                        color = Color.White.copy(alpha = 0.78f),
                        fontFamily = family,
                        fontSize = labelSize.sp,
                        fontWeight = FontWeight.Medium,
                        letterSpacing = 1.1.sp,
                        maxLines = 1,
                        style = TextStyle(shadow = StatShadow),
                    )
                }
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(
                        item.value.ifBlank { " " },
                        color = Color.White,
                        fontFamily = family,
                        fontSize = size.sp,
                        fontWeight = FontWeight.Medium,
                        maxLines = 1,
                        style = TextStyle(shadow = StatShadow),
                    )
                    item.sub?.let { sub ->
                        Text(
                            sub,
                            color = Color.White.copy(alpha = 0.88f),
                            fontFamily = family,
                            fontSize = size.sp,
                            fontWeight = FontWeight.Medium,
                            maxLines = 1,
                            style = TextStyle(shadow = StatShadow),
                        )
                    }
                }
            }
        }
    }
}

private fun formatPercent(value: Double): String = "${value.roundToInt()}%"

private fun formatTemp(value: Double): String = String.format(Locale.US, "%.0f°", value)

@Composable
private fun WeatherChip(
    weather: WeatherInfo,
    interactive: Boolean = true,
    onLongPress: () -> Unit = {},
) {
    StatusChip(
        icon = Icons.Outlined.Cloud,
        title = String.format(Locale.US, "%.1f°", weather.celsius),
        subtitle = "${weather.condition}  ${weather.city}",
        interactive = interactive,
        onLongPress = onLongPress,
    )
}

@Composable
private fun IndoorChip(
    celsius: Double,
    humidity: Double?,
    name: String,
    online: Boolean,
    interactive: Boolean = true,
    onLongPress: () -> Unit = {},
) {
    val line = buildString {
        append(name)
        humidity?.let { append("  ${it.roundToInt()}%") }
        if (!online) append("  离线")
    }
    StatusChip(
        icon = Icons.Outlined.Thermostat,
        title = String.format(Locale.US, "%.1f°", celsius),
        subtitle = line,
        dimmed = !online,
        interactive = interactive,
        onLongPress = onLongPress,
    )
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun StatusChip(
    icon: ImageVector,
    title: String,
    subtitle: String,
    dimmed: Boolean = false,
    interactive: Boolean = true,
    onLongPress: () -> Unit = {},
) {
    val colors = MaterialTheme.colorScheme
    val type = LocalTypeLook.current
    val family = type.font.toFamily()
    val size = type.chipSize
    TilePanel(
        modifier = Modifier
            .fillMaxSize()
            .alpha(if (dimmed) 0.55f else 1f)
            .combinedClickable(
                enabled = interactive,
                indication = null,
                interactionSource = remember { MutableInteractionSource() },
                onClick = {},
                onLongClick = onLongPress,
            ),
    ) {
        Row(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 14.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(icon, contentDescription = null, tint = colors.primary, modifier = Modifier.size(18.dp))
            Column {
                Text(
                    title,
                    color = Color.White,
                    fontFamily = family,
                    fontSize = size.sp,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                    style = TextStyle(shadow = StatShadow),
                )
                Text(
                    subtitle,
                    color = Color.White.copy(alpha = 0.78f),
                    fontFamily = family,
                    fontSize = (size * 0.78f).sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    style = TextStyle(shadow = StatShadow),
                )
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun MediaTile(
    media: MediaInfo,
    interactive: Boolean = true,
    onAction: (String) -> Unit,
    onLongPress: () -> Unit = {},
) {
    val colors = MaterialTheme.colorScheme
    val type = LocalTypeLook.current
    val family = type.font.toFamily()
    val size = type.tileSize
    val lit = media.playing
    val title = media.title?.takeIf { it.isNotBlank() } ?: if (media.playing) "正在播放" else "未在播放"
    val subtitle = buildList {
        media.artist?.takeIf { it.isNotBlank() }?.let(::add)
        add(media.app?.takeIf { it.isNotBlank() } ?: "手机")
    }.joinToString("  ·  ")

    TilePanel(
        modifier = Modifier
            .fillMaxSize()
            .combinedClickable(
                enabled = interactive,
                indication = null,
                interactionSource = remember { MutableInteractionSource() },
                onClick = {},
                onLongClick = onLongPress,
            ),
        highlighted = lit,
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.SpaceBetween,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    painter = painterResource(R.drawable.ri_music),
                    contentDescription = null,
                    tint = if (lit) colors.primary else Color.White.copy(alpha = 0.88f),
                    modifier = Modifier.size(18.dp),
                )
                Spacer(Modifier.width(8.dp))
                Column(Modifier.weight(1f)) {
                    Text(title, color = Color.White, fontFamily = family, fontSize = size.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(subtitle, color = Color.White.copy(alpha = 0.78f), fontFamily = family, fontSize = (size * 0.78f).sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                MediaButton(R.drawable.ri_skip_back, interactive, { onAction("previous") }, "上一首")
                MediaButton(
                    if (media.playing) R.drawable.ri_pause else R.drawable.ri_play,
                    interactive,
                    { onAction("toggle") },
                    if (media.playing) "暂停" else "播放",
                    emphasized = true,
                )
                MediaButton(R.drawable.ri_skip_forward, interactive, { onAction("next") }, "下一首")
            }
        }
    }
}

@Composable
private fun MediaButton(
    drawable: Int,
    enabled: Boolean,
    onClick: () -> Unit,
    label: String,
    emphasized: Boolean = false,
) {
    Box(
        modifier = Modifier
            .size(if (emphasized) 38.dp else 34.dp)
            .clip(CircleShape)
            .background(if (emphasized) Color.White.copy(alpha = 0.28f) else Color.White.copy(alpha = 0.14f))
            .clickable(
                enabled = enabled,
                role = Role.Button,
                indication = null,
                interactionSource = remember { MutableInteractionSource() },
                onClick = onClick,
            ),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            painter = painterResource(drawable),
            contentDescription = label,
            tint = if (emphasized) Color.White else Color.White.copy(alpha = 0.88f),
            modifier = Modifier.size(if (emphasized) 22.dp else 18.dp),
        )
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun DeviceTile(
    device: HubDevice,
    busy: Boolean,
    interactive: Boolean = true,
    onToggle: () -> Unit,
    onLongPress: () -> Unit = {},
) {
    val colors = MaterialTheme.colorScheme
    val look = LocalTypeLook.current
    val family = look.font.toFamily()
    val size = look.tileSize
    val type = device.deviceType()
    val lit = device.on && device.online
    TilePanel(
        modifier = Modifier
            .fillMaxSize()
            .alpha(if (device.online) 1f else 0.5f)
            .combinedClickable(
                enabled = interactive && device.online && !busy && type != DeviceType.Unknown,
                role = Role.Switch,
                indication = null,
                interactionSource = remember { MutableInteractionSource() },
                onClick = onToggle,
                onLongClick = onLongPress,
            ),
        highlighted = lit,
    ) {
        Row(
            modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(40.dp)
                    .then(
                        if (LocalTileChrome.current) {
                            Modifier.background(
                                if (lit) colors.primary.copy(alpha = 0.28f) else Color.White.copy(alpha = 0.12f),
                                CircleShape,
                            )
                        } else {
                            Modifier
                        },
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector = if (type == DeviceType.Light) Icons.Outlined.Lightbulb else Icons.Outlined.PowerSettingsNew,
                    contentDescription = null,
                    tint = if (lit) colors.primary else Color.White.copy(alpha = 0.88f),
                    modifier = Modifier.size(22.dp),
                )
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(device.name, color = Color.White, fontFamily = family, fontSize = size.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(
                    when {
                        !device.online -> "离线"
                        busy -> "执行中"
                        device.on && type == DeviceType.Light && device.brightness != null -> "开  ${device.brightness}%"
                        device.on -> "开"
                        else -> "关"
                    },
                    color = Color.White.copy(alpha = 0.78f),
                    fontFamily = family,
                    fontSize = (size * 0.88f).sp,
                )
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun WinTile(
    app: WinApp,
    selected: Boolean,
    busy: Boolean,
    interactive: Boolean = true,
    onClick: () -> Unit,
    onLongPress: () -> Unit = {},
) {
    val colors = MaterialTheme.colorScheme
    val look = LocalTypeLook.current
    val family = look.font.toFamily()
    val size = look.tileSize
    TilePanel(
        modifier = Modifier
            .fillMaxSize()
            .alpha(if (app.online) 1f else 0.5f)
            .combinedClickable(
                enabled = interactive && app.online && !busy,
                role = Role.Button,
                indication = null,
                interactionSource = remember { MutableInteractionSource() },
                onClick = onClick,
                onLongClick = onLongPress,
            ),
        highlighted = selected,
    ) {
        Row(
            modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val tint = if (selected) colors.primary else Color.White.copy(alpha = 0.88f)
            val remix = RemixIcons.drawableFor(app.icon ?: app.id)
            Box(
                modifier = Modifier
                    .size(40.dp)
                    .then(
                        if (LocalTileChrome.current) {
                            Modifier.background(
                                if (selected) colors.primary.copy(alpha = 0.28f) else Color.White.copy(alpha = 0.12f),
                                CircleShape,
                            )
                        } else {
                            Modifier
                        },
                    ),
                contentAlignment = Alignment.Center,
            ) {
                if (remix != null) {
                    Icon(painter = painterResource(remix), contentDescription = null, tint = tint, modifier = Modifier.size(22.dp))
                } else {
                    Text(app.name.take(1), color = tint, fontFamily = family, fontSize = size.sp, fontWeight = FontWeight.Medium)
                }
            }
            Spacer(Modifier.width(10.dp))
            Text(
                when {
                    busy -> "启动中"
                    !app.online -> "离线"
                    app.running -> app.name
                    else -> app.name
                },
                color = Color.White,
                fontFamily = family,
                fontSize = size.sp,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

@Composable
private fun WakeListeningChip(
    text: String,
    settled: Boolean,
    modifier: Modifier = Modifier,
) {
    val colors = MaterialTheme.colorScheme
    val type = LocalTypeLook.current
    val family = type.font.toFamily()
    val size = type.chipSize
    val infinite = rememberInfiniteTransition(label = "wake")
    val pulse = infinite.animateFloat(
        initialValue = 0.45f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(700),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "pulse",
    )
    TilePanel(
        modifier = modifier.widthIn(max = 280.dp),
        highlighted = !settled,
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(
                Icons.Outlined.Mic,
                contentDescription = null,
                tint = colors.primary.copy(
                    alpha = if (settled) 0.55f else 0.50f + pulse.value * 0.50f,
                ),
                modifier = Modifier.size(18.dp),
            )
            Text(
                text.ifBlank { if (settled) "没听清" else "在听" },
                color = Color.White,
                fontFamily = family,
                fontSize = size.sp,
                fontWeight = FontWeight.Medium,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                style = TextStyle(shadow = StatShadow),
            )
        }
    }
}

@Composable
private fun CompanionCaption(
    text: String,
    modifier: Modifier = Modifier,
) {
    Text(
        text,
        color = Color.White.copy(alpha = 0.92f),
        fontSize = 14.sp,
        fontWeight = FontWeight.Medium,
        maxLines = 2,
        overflow = TextOverflow.Ellipsis,
        style = TextStyle(shadow = StatShadow),
        modifier = modifier,
    )
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
                media = DemoSnapshot.media,
                weather = DemoSnapshot.weather,
            ),
            onOpenSettings = {},
            onPower = { _, _ -> },
            onWinClick = {},
            onMedia = {},
        )
    }
}
