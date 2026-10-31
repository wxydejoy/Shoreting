package cn.weiekko.dock.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Lightbulb
import androidx.compose.material.icons.outlined.PowerSettingsNew
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import cn.weiekko.dock.data.DeviceType
import cn.weiekko.dock.data.HubDevice
import cn.weiekko.dock.data.WinApp
import cn.weiekko.dock.data.deviceType

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
