package cn.weiekko.dock.ui

import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.offset
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.text.PlatformTextStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.min
import kotlin.math.roundToInt

private val TimeFmt = DateTimeFormatter.ofPattern("HH:mm")
private val DateFmt = DateTimeFormatter.ofPattern("M月d日 EEEE", Locale.CHINA)

/** 时钟磁贴：整个主屏最大的一块字，位置和字号跟着布局矩形走。 */
@Composable
internal fun ClockModule(
    frameW: Float,
    frameH: Float,
    clockScalePercent: Int,
    drift: PixelShift,
    timeShadow: Shadow,
    weatherLine: String?,
    editing: Boolean,
    onSelect: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    val colors = MaterialTheme.colorScheme
    val family = LocalTypeLook.current.font.toFamily()
    val timeSp = min(frameW * 0.50f, frameH * 0.68f) * (clockScalePercent / 100f)
    val now = rememberNow()
    Box(
        modifier = Modifier
            .fillMaxSize()
            .combinedClickable(
                indication = null,
                interactionSource = remember { MutableInteractionSource() },
                onClick = {
                    if (editing) onSelect() else onOpenSettings()
                },
                onLongClick = onSelect,
            ),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                // 防烧屏：时钟每 5 分钟在 ±2px 内挪一步
                .offset {
                    IntOffset(
                        drift.x.dp.toPx().roundToInt(),
                        drift.y.dp.toPx().roundToInt(),
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
                    fontSize = (LocalTypeLook.current.chipSize + 1).sp,
                    fontWeight = FontWeight.Medium,
                    color = Color.White.copy(alpha = 0.86f),
                    maxLines = 1,
                    style = TextStyle(shadow = timeShadow),
                )
                if (weatherLine != null) {
                    Text(
                        "  ·  ",
                        fontFamily = family,
                        fontSize = (LocalTypeLook.current.chipSize + 1).sp,
                        fontWeight = FontWeight.Medium,
                        color = Color.White.copy(alpha = 0.46f),
                        style = TextStyle(shadow = timeShadow),
                    )
                    Text(
                        weatherLine,
                        fontFamily = family,
                        fontSize = (LocalTypeLook.current.chipSize + 1).sp,
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
