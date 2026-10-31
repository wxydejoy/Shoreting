package cn.weiekko.dock.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Cloud
import androidx.compose.material.icons.outlined.Thermostat
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import cn.weiekko.dock.data.PcStatus
import cn.weiekko.dock.data.WeatherInfo
import java.util.Locale
import kotlin.math.roundToInt

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
