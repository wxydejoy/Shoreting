package cn.weiekko.dock.ui

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import cn.weiekko.dock.data.TimerStatus
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.StateFlow

/** 主屏最上面那层浮层：护暗、字幕、镜像、访客角标、睡眠黑幕、对话呼吸光、听写胶囊。 */
@Composable
internal fun BoxScope.ScreenOverlays(
    state: DockUiState,
    voice: StateFlow<VoiceState>,
    voiceListening: Boolean,
    dim: Float,
    editing: Boolean,
) {
    if (dim < 1f) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black.copy(alpha = 1f - dim))
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
    if (mirrorLine != null && !state.hubSleeping && !editing) {
        MirrorBanner(
            text = mirrorLine,
            modifier = Modifier
                .align(Alignment.TopCenter)
                .padding(top = 10.dp)
                .zIndex(16f),
        )
    }

    // 访客模式（F26）：开着的时候她自己知道收着点，屏上留个角标提醒主人
    if (state.snapshot?.guest != null && !state.hubSleeping && !editing) {
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
    if (state.conversationOpen && !state.hubSleeping && !editing) {
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

/** 通知镜像（F35）：顶部一条窄横幅，亮一下就走。 */
@Composable
private fun MirrorBanner(
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
private fun GuestBadge(
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

/** ASR 半句只在这里收，只有这颗胶囊随它重组。 */
@Composable
private fun LiveWakeListeningChip(
    voice: StateFlow<VoiceState>,
    modifier: Modifier = Modifier,
) {
    val state by voice.collectAsStateWithLifecycle()
    WakeListeningChip(text = state.text, settled = state.settled, modifier = modifier)
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

private fun formatTimer(seconds: Int): String {
    val s = seconds.coerceAtLeast(0)
    val h = s / 3600
    val m = (s % 3600) / 60
    val sec = s % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, sec) else "%02d:%02d".format(m, sec)
}
