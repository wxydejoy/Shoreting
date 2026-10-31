package cn.weiekko.dock.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import cn.weiekko.dock.R
import cn.weiekko.dock.data.MediaInfo
import kotlinx.coroutines.flow.StateFlow

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
            verticalArrangement = androidx.compose.foundation.layout.Arrangement.SpaceBetween,
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
                horizontalArrangement = androidx.compose.foundation.layout.Arrangement.SpaceEvenly,
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
