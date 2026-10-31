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

// ---- 磁贴与胶囊（从 HomeScreen.kt 拆出）----
internal val StatShadow = Shadow(color = Color.Black.copy(alpha = 0.95f), offset = Offset(0f, 2f), blurRadius = 22f)

@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun PcMonitorRow(
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

internal data class PcStatColumn(
    val label: String,
    val top: String,
    val topSub: String? = null,
    val bottom: String = "",
    val bottomSub: String? = null,
    val value: String = top,
    val sub: String? = topSub,
)

internal fun pcStatColumns(pc: PcStatus?, mini: PcStatus?, includeFps: Boolean): List<PcStatColumn> {
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
internal fun PcStatRow(
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

internal fun formatPercent(value: Double): String = "${value.roundToInt()}%"

internal fun formatTemp(value: Double): String = String.format(Locale.US, "%.0f°", value)

@Composable
internal fun WeatherChip(
    weather: WeatherInfo,
    interactive: Boolean = true,
    onLongPress: () -> Unit = {},
) {
    StatusChip(
        icon = Icons.Outlined.Cloud,
        title = String.format(Locale.US, "%.1f°", weather.celsius),
        subtitle = listOfNotNull("${weather.condition}  ${weather.city}", weather.airLine())
            .joinToString("  "),
        interactive = interactive,
        onLongPress = onLongPress,
    )
}

@Composable
internal fun IndoorChip(
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
internal fun StatusChip(
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
internal fun MediaTile(
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
internal fun MediaButton(
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

/** 右下角一列场景（F7）：点一下让 Hub 把一组设备一次做完。 */
@Composable
internal fun SceneDock(
    scenes: List<Scene>,
    busy: Set<String>,
    onRun: (Scene) -> Unit,
    modifier: Modifier = Modifier,
) {
    val type = LocalTypeLook.current
    val family = type.font.toFamily()
    Column(
        modifier = modifier,
        horizontalAlignment = Alignment.End,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        scenes.take(4).forEach { scene ->
            val working = scene.id in busy
            Surface(
                shape = RoundedCornerShape(50),
                color = Color.White.copy(alpha = if (working) 0.08f else 0.18f),
                modifier = Modifier
                    .clip(RoundedCornerShape(50))
                    .clickable(enabled = !working) { onRun(scene) },
            ) {
                Text(
                    text = if (working) "…" else scene.name,
                    fontFamily = family,
                    fontSize = type.chipSize.sp,
                    fontWeight = FontWeight.Medium,
                    color = Color.White.copy(alpha = if (working) 0.55f else 0.94f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    textAlign = TextAlign.End,
                    modifier = Modifier
                        .widthIn(max = 132.dp)
                        .padding(horizontal = 14.dp, vertical = 9.dp),
                )
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun DeviceTile(
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
internal fun WinTile(
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

