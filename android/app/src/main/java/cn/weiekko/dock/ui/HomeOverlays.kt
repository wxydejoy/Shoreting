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

// ---- 浮层：字幕/镜像/角标/番茄环/听写胶囊（从 HomeScreen.kt 拆出）----
/** 每秒一次的媒体状态只在这里收，主屏本体不跟着重组。 */
@Composable
internal fun LiveMediaTile(
    media: StateFlow<MediaInfo>,
    interactive: Boolean,
    onAction: (String) -> Unit,
    onLongPress: () -> Unit = {},
) {
    val info by media.collectAsStateWithLifecycle()
    MediaTile(
        media = info,
        interactive = interactive,
        onAction = onAction,
        onLongPress = onLongPress,
    )
}

/** ASR 半句只在这里收，只有这颗胶囊随它重组。 */
@Composable
internal fun LiveWakeListeningChip(
    voice: StateFlow<VoiceState>,
    modifier: Modifier = Modifier,
) {
    val state by voice.collectAsStateWithLifecycle()
    WakeListeningChip(text = state.text, settled = state.settled, modifier = modifier)
}

@Composable
internal fun WakeListeningChip(
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
internal fun CompanionCaption(
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

/** 通知镜像（F35）：顶部一条窄横幅，亮一下就走。 */
@Composable
internal fun MirrorBanner(
    text: String,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier,
        color = Color.Black.copy(alpha = 0.55f),
        shape = MaterialTheme.shapes.small,
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text,
                color = Color.White.copy(alpha = 0.88f),
                fontSize = 12.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                style = TextStyle(shadow = StatShadow),
            )
        }
    }
}

/** 访客模式（F26）：右上角一个小角标，记录都收着呢——主人的底气，不是访客的提示。 */
@Composable
internal fun GuestBadge(
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier,
        color = Color.Black.copy(alpha = 0.45f),
        shape = MaterialTheme.shapes.small,
    ) {
        Text(
            "访客",
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
            color = Color.White.copy(alpha = 0.6f),
            fontSize = 11.sp,
            style = TextStyle(shadow = StatShadow),
        )
    }
}

/** 番茄钟细环（F9）：倒计时走环、正计时满环，中间是时间。 */
@Composable
internal fun TimerRing(
    timer: TimerStatus,
    modifier: Modifier = Modifier,
) {
    // 两次快照之间本地每秒续走：key 换新快照时归零重数
    val tick by produceState(0, timer) {
        while (true) {
            kotlinx.coroutines.delay(1_000)
            value++
        }
    }
    val progress: Float
    val shown: Int
    if (timer.isStopwatch) {
        progress = 1f
        shown = timer.seconds + tick
    } else {
        shown = (timer.secondsLeft - tick).coerceAtLeast(0)
        progress = if (timer.totalSec > 0) shown.toFloat() / timer.totalSec else 0f
    }
    val track = Color.White.copy(alpha = 0.16f)
    val ring = Color.White.copy(alpha = 0.85f)
    Box(modifier = modifier.size(64.dp), contentAlignment = Alignment.Center) {
        Canvas(modifier = Modifier.fillMaxSize()) {
            val stroke = 3.dp.toPx()
            val inset = stroke / 2
            val arcSize = Size(size.width - stroke, size.height - stroke)
            drawArc(
                color = track,
                startAngle = -90f,
                sweepAngle = 360f,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = arcSize,
                style = Stroke(width = stroke, cap = StrokeCap.Round),
            )
            drawArc(
                color = ring,
                startAngle = -90f,
                sweepAngle = 360f * progress.coerceIn(0f, 1f),
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = arcSize,
                style = Stroke(width = stroke, cap = StrokeCap.Round),
            )
        }
        Text(
            formatTimer(shown),
            color = Color.White.copy(alpha = 0.9f),
            fontSize = 13.sp,
            fontWeight = FontWeight.Medium,
            style = TextStyle(shadow = StatShadow),
        )
    }
}

internal fun formatTimer(seconds: Int): String {
    val s = seconds.coerceAtLeast(0)
    val h = s / 3600
    val m = (s % 3600) / 60
    val sec = s % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, sec) else "%02d:%02d".format(m, sec)
}

