package cn.weiekko.dock.ui

import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import cn.weiekko.dock.data.DockFont
import cn.weiekko.dock.data.DockModule
import cn.weiekko.dock.data.HubConnection
import cn.weiekko.dock.data.MiniConnection
import cn.weiekko.dock.data.HubPreferences
import cn.weiekko.dock.data.TileLook
import cn.weiekko.dock.data.TileStyle
import cn.weiekko.dock.data.TypeLook
import kotlin.math.roundToInt

@Composable
fun SettingsScreen(
    state: DockUiState,
    canGoBack: Boolean,
    onTest: (host: String, port: String, token: String) -> Unit,
    onSave: (host: String, port: String, token: String) -> Unit,
    onTestMini: (host: String, port: String, token: String) -> Unit = { _, _, _ -> },
    onSaveMini: (host: String, port: String, token: String) -> Unit = { _, _, _ -> },
    onSetVideo: (Uri?) -> Unit,
    onSetTileLook: (TileLook) -> Unit,
    onSetTypeLook: (TypeLook) -> Unit,
    onSetPowerScreen: (Boolean) -> Unit,
    onSetHubSleepDelay: (Int) -> Unit = {},
    onSetHubReconnect: (Int) -> Unit = {},
    onSetWeatherEnabled: (Boolean) -> Unit = {},
    onSetWeatherCity: (String) -> Unit = {},
    onSetWakeWord: (Boolean) -> Unit = {},
    onSetHubEnabled: (Boolean) -> Unit = {},
    onEditLayout: () -> Unit = {},
    onSetModuleVisible: (DockModule, Boolean) -> Unit = { _, _ -> },
    onSetModuleChrome: (DockModule, Boolean) -> Unit = { _, _ -> },
    onResetLayout: () -> Unit = {},
    onBack: () -> Unit,
) {
    var host by rememberSaveable { mutableStateOf(state.connection.host) }
    var port by rememberSaveable {
        mutableStateOf(state.connection.port.toString())
    }
    var token by rememberSaveable { mutableStateOf(state.connection.token) }
    var miniHost by rememberSaveable { mutableStateOf(state.miniConnection.host.ifBlank { MiniConnection.DEFAULT_HOST }) }
    var miniPort by rememberSaveable { mutableStateOf(state.miniConnection.port.toString()) }
    var miniToken by rememberSaveable { mutableStateOf(state.miniConnection.token.ifBlank { MiniConnection.DEFAULT_TOKEN }) }
    var weatherCity by rememberSaveable { mutableStateOf(state.weatherCity) }
    val context = LocalContext.current
    val pickVideo = rememberLauncherForActivityResult(
        object : ActivityResultContracts.OpenDocument() {
            override fun createIntent(
                context: android.content.Context,
                input: Array<String>,
            ): Intent {
                return super.createIntent(context, input).addFlags(
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or
                        Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION,
                )
            }
        },
    ) { uri ->
        uri?.let(onSetVideo)
    }
    val videoName = remember(state.backgroundVideoUri) {
        state.backgroundVideoUri?.let { displayName(context, Uri.parse(it)) }
    }
    val colors = MaterialTheme.colorScheme
    val fieldColors = OutlinedTextFieldDefaults.colors(
        focusedBorderColor = colors.primary,
        unfocusedBorderColor = colors.outline,
        focusedLabelColor = colors.primary,
        cursorColor = colors.primary,
    )

    LaunchedEffect(state.connection, state.miniConnection, state.weatherCity) {
        if (host.isEmpty()) host = state.connection.host
        if (token.isEmpty()) token = state.connection.token
        if (port == HubConnection.DEFAULT_PORT.toString() && state.connection.port != HubConnection.DEFAULT_PORT) {
            port = state.connection.port.toString()
        }
        if (miniHost.isEmpty()) miniHost = state.miniConnection.host
        if (miniToken.isEmpty()) miniToken = state.miniConnection.token
        if (miniPort == MiniConnection.DEFAULT_PORT.toString() &&
            state.miniConnection.port != MiniConnection.DEFAULT_PORT
        ) {
            miniPort = state.miniConnection.port.toString()
        }
        if (weatherCity.isEmpty() && state.weatherCity.isNotEmpty()) {
            weatherCity = state.weatherCity
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.background)
            .imePadding(),
        contentAlignment = Alignment.TopCenter,
    ) {
    Column(
        modifier = Modifier
            .widthIn(max = 780.dp)
            .fillMaxHeight()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.Top,
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (canGoBack) {
                IconButton(onClick = {
                    onSave(host, port, token)
                    onSaveMini(miniHost, miniPort, miniToken)
                    onBack()
                }) {
                    Icon(
                        imageVector = Icons.AutoMirrored.Outlined.ArrowBack,
                        contentDescription = "返回主屏",
                        tint = colors.onSurfaceVariant,
                    )
                }
            }
            Text(
                "设置",
                style = MaterialTheme.typography.headlineMedium,
                modifier = Modifier.padding(start = 4.dp),
            )
        }
        Spacer(Modifier.height(8.dp))
        Text(
            "Hub = 运行岸亭 Hub 的那台机器（现在 ${HubConnection.DEFAULT_HOST}:${HubConnection.DEFAULT_PORT}）。Mac 本机监控会自动跟随这个地址。",
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.padding(horizontal = 4.dp),
        )
        Spacer(Modifier.height(18.dp))
        Text(
            "连接",
            style = MaterialTheme.typography.labelLarge,
            color = colors.primary,
            modifier = Modifier.padding(start = 6.dp),
        )
        Spacer(Modifier.height(6.dp))
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = colors.surface,
            shape = MaterialTheme.shapes.large,
        ) {
            Column(Modifier.padding(20.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text("连接 Hub", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(4.dp))
                        Text(
                            if (state.hubEnabled) {
                                "已开。主屏跟这台 Hub 同步。"
                            } else {
                                "已关。主屏只用手机上的时钟、媒体和天气。地址仍留在下面。"
                            },
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                    Switch(
                        checked = state.hubEnabled,
                        onCheckedChange = onSetHubEnabled,
                        colors = SwitchDefaults.colors(
                            checkedThumbColor = colors.onPrimary,
                            checkedTrackColor = colors.primary,
                        ),
                    )
                }
                Spacer(Modifier.height(16.dp))
                Text("岸亭 Hub", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = host,
                    onValueChange = { host = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("地址") },
                    placeholder = { Text(HubConnection.DEFAULT_HOST) },
                    singleLine = true,
                    shape = MaterialTheme.shapes.small,
                    colors = fieldColors,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                )
                Spacer(Modifier.height(14.dp))
                OutlinedTextField(
                    value = port,
                    onValueChange = { port = it.filter { ch -> ch.isDigit() }.take(5) },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("端口") },
                    placeholder = { Text("17890") },
                    singleLine = true,
                    shape = MaterialTheme.shapes.small,
                    colors = fieldColors,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
                Spacer(Modifier.height(14.dp))
                OutlinedTextField(
                    value = token,
                    onValueChange = { token = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("Token") },
                    singleLine = true,
                    shape = MaterialTheme.shapes.small,
                    colors = fieldColors,
                    visualTransformation = PasswordVisualTransformation(),
                )
            }
        }
        Spacer(Modifier.height(10.dp))
        Button(
            onClick = { onTest(host, port, token) },
            enabled = !state.testing,
            modifier = Modifier
                .fillMaxWidth()
                .height(52.dp),
            shape = MaterialTheme.shapes.medium,
            colors = ButtonDefaults.buttonColors(
                containerColor = colors.primary,
                contentColor = colors.onPrimary,
                disabledContainerColor = colors.primary.copy(alpha = 0.4f),
                disabledContentColor = colors.onPrimary,
            ),
        ) {
            if (state.testing) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    strokeWidth = 2.dp,
                    color = colors.onPrimary,
                )
                Spacer(Modifier.width(10.dp))
                Text("正在连接…")
            } else {
                Text("测试 Hub")
            }
        }
        Spacer(Modifier.height(20.dp))
        state.settingsStatus?.let { status ->
            Spacer(Modifier.height(16.dp))
            Surface(
                modifier = Modifier.fillMaxWidth(),
                color = if (state.settingsOk) colors.primary.copy(alpha = 0.12f) else colors.errorContainer,
                shape = MaterialTheme.shapes.medium,
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(
                        imageVector = if (state.settingsOk) {
                            Icons.Outlined.CheckCircle
                        } else {
                            Icons.Outlined.ErrorOutline
                        },
                        contentDescription = null,
                        tint = if (state.settingsOk) colors.secondary else colors.error,
                        modifier = Modifier.size(20.dp),
                    )
                    Spacer(Modifier.width(10.dp))
                    Text(
                        status,
                        color = if (state.settingsOk) colors.secondary else colors.onErrorContainer,
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        }
        Spacer(Modifier.height(20.dp))
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = colors.surface,
            shape = MaterialTheme.shapes.large,
        ) {
            Column(Modifier.padding(20.dp)) {
                Text(
                    "视频背景",
                    style = MaterialTheme.typography.titleMedium,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    videoName ?: "未设置，主屏为纯色。循环静音播放，选横屏视频效果更好。",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(14.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Button(
                        onClick = { pickVideo.launch(arrayOf("video/*")) },
                        modifier = Modifier.weight(1f).height(48.dp),
                        shape = MaterialTheme.shapes.medium,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = colors.primary,
                            contentColor = colors.onPrimary,
                        ),
                    ) {
                        Text(if (videoName == null) "选择视频" else "更换视频")
                    }
                    OutlinedButton(
                        onClick = { onSetVideo(null) },
                        enabled = videoName != null,
                        modifier = Modifier.height(48.dp),
                        shape = MaterialTheme.shapes.medium,
                    ) {
                        Text("清除")
                    }
                }
            }
        }
        Spacer(Modifier.height(20.dp))
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = colors.surface,
            shape = MaterialTheme.shapes.large,
        ) {
            Column(Modifier.padding(20.dp)) {
                Text("主屏布局", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(6.dp))
                Text(
                    "长按主屏任意模块进入编排：拖动移动，右下角缩放。选中后可隐藏模块，或去掉背景边框。",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(14.dp))
                Button(
                    onClick = onEditLayout,
                    modifier = Modifier.fillMaxWidth().height(48.dp),
                    shape = MaterialTheme.shapes.medium,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = colors.primary,
                        contentColor = colors.onPrimary,
                    ),
                ) {
                    Text("编辑布局")
                }
                Spacer(Modifier.height(8.dp))
                OutlinedButton(
                    onClick = {
                        context.startActivity(
                            Intent(android.provider.Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                        )
                    },
                    modifier = Modifier.fillMaxWidth().height(48.dp),
                    shape = MaterialTheme.shapes.medium,
                ) {
                    Text("媒体通知使用权")
                }
                Spacer(Modifier.height(4.dp))
                DockModule.entries.forEach { module ->
                    val rect = state.layout.rect(module)
                    val visible = rect?.visible ?: true
                    val chrome = rect?.chrome ?: true
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            module.label,
                            style = MaterialTheme.typography.bodyLarge,
                            modifier = Modifier.weight(1f),
                        )
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text(
                                "显示",
                                style = MaterialTheme.typography.labelSmall,
                                color = colors.onSurfaceVariant,
                            )
                            Switch(
                                checked = visible,
                                onCheckedChange = { onSetModuleVisible(module, it) },
                                colors = SwitchDefaults.colors(
                                    checkedThumbColor = colors.onPrimary,
                                    checkedTrackColor = colors.primary,
                                ),
                            )
                        }
                        Spacer(Modifier.width(10.dp))
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text(
                                "背景",
                                style = MaterialTheme.typography.labelSmall,
                                color = colors.onSurfaceVariant,
                            )
                            Switch(
                                checked = chrome,
                                onCheckedChange = { onSetModuleChrome(module, it) },
                                colors = SwitchDefaults.colors(
                                    checkedThumbColor = colors.onPrimary,
                                    checkedTrackColor = colors.primary,
                                ),
                            )
                        }
                    }
                }
                OutlinedButton(
                    onClick = onResetLayout,
                    modifier = Modifier.fillMaxWidth().height(48.dp),
                    shape = MaterialTheme.shapes.medium,
                ) {
                    Text("恢复默认布局")
                }
            }
        }
        Spacer(Modifier.height(20.dp))
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = colors.surface,
            shape = MaterialTheme.shapes.large,
        ) {
            Column(Modifier.padding(20.dp)) {
                Text(
                    "外观与字体",
                    style = MaterialTheme.typography.titleMedium,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    "主屏左右按钮、监控条和底部芯片都会跟着变。拖动即可预览。",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(14.dp))
                Row(
                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    TileStyle.entries.forEach { style ->
                        val selected = state.tileLook.style == style
                        FilterChip(
                            selected = selected,
                            onClick = { onSetTileLook(state.tileLook.copy(style = style)) },
                            label = { Text(style.label) },
                            colors = FilterChipDefaults.filterChipColors(
                                selectedContainerColor = colors.primary.copy(alpha = 0.28f),
                                selectedLabelColor = colors.onBackground,
                                containerColor = colors.surfaceVariant,
                                labelColor = colors.onSurfaceVariant,
                            ),
                        )
                    }
                }
                Spacer(Modifier.height(14.dp))
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(MaterialTheme.shapes.medium)
                        .background(Color(0xFF12161C))
                        .padding(12.dp),
                ) {
                    CompositionLocalProvider(LocalTileLook provides state.tileLook) {
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            TilePanel(
                                modifier = Modifier
                                    .weight(1f)
                                    .height(56.dp),
                            ) {
                                Text(
                                    "Chrome",
                                    color = Color.White,
                                    fontSize = 15.sp,
                                    fontWeight = FontWeight.Medium,
                                    modifier = Modifier
                                        .align(Alignment.CenterStart)
                                        .padding(horizontal = 14.dp),
                                )
                            }
                            TilePanel(
                                modifier = Modifier
                                    .weight(1f)
                                    .height(56.dp),
                                highlighted = true,
                            ) {
                                Text(
                                    "台灯  开",
                                    color = Color.White,
                                    fontSize = 15.sp,
                                    fontWeight = FontWeight.Medium,
                                    modifier = Modifier
                                        .align(Alignment.CenterStart)
                                        .padding(horizontal = 14.dp),
                                )
                            }
                        }
                    }
                }
                Spacer(Modifier.height(18.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text("透明度", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "${100 - state.tileLook.opacityPercent}%",
                        style = MaterialTheme.typography.bodyMedium,
                        color = colors.onBackground,
                    )
                }
                Slider(
                    value = (100 - state.tileLook.opacityPercent).toFloat(),
                    onValueChange = { value ->
                        onSetTileLook(
                            state.tileLook.copy(opacityPercent = 100 - value.roundToInt()),
                        )
                    },
                    valueRange = TileLook.OPACITY_MIN.toFloat()..TileLook.OPACITY_MAX.toFloat(),
                    colors = SliderDefaults.colors(
                        thumbColor = colors.primary,
                        activeTrackColor = colors.primary,
                        inactiveTrackColor = colors.outline,
                    ),
                )
                Spacer(Modifier.height(4.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text("圆角", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "${state.tileLook.cornerDp}",
                        style = MaterialTheme.typography.bodyMedium,
                        color = colors.onBackground,
                    )
                }
                Slider(
                    value = state.tileLook.cornerDp.toFloat(),
                    onValueChange = { value ->
                        onSetTileLook(state.tileLook.copy(cornerDp = value.roundToInt()))
                    },
                    valueRange = TileLook.CORNER_MIN.toFloat()..TileLook.CORNER_MAX.toFloat(),
                    colors = SliderDefaults.colors(
                        thumbColor = colors.primary,
                        activeTrackColor = colors.primary,
                        inactiveTrackColor = colors.outline,
                    ),
                )
            }
        }
        Spacer(Modifier.height(20.dp))
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = colors.surface,
            shape = MaterialTheme.shapes.large,
        ) {
            Column(Modifier.padding(20.dp)) {
                Text("字体", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(6.dp))
                Text(
                    "系统字体和内置开源字体。中文在 Inter / Outfit / JetBrains Mono 上会回退到系统字形。",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(12.dp))
                Row(
                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    DockFont.entries.forEach { font ->
                        val selected = state.typeLook.font == font
                        FilterChip(
                            selected = selected,
                            onClick = { onSetTypeLook(state.typeLook.copy(font = font)) },
                            label = {
                                Text(font.label, fontFamily = font.toFamily())
                            },
                            colors = FilterChipDefaults.filterChipColors(
                                selectedContainerColor = colors.primary.copy(alpha = 0.28f),
                                selectedLabelColor = colors.onBackground,
                                containerColor = colors.surfaceVariant,
                                labelColor = colors.onSurfaceVariant,
                            ),
                        )
                    }
                }
                Spacer(Modifier.height(12.dp))
                CompositionLocalProvider(LocalTypeLook provides state.typeLook) {
                    Text(
                        "19:30  CPU 24%",
                        color = colors.onBackground,
                        fontFamily = state.typeLook.font.toFamily(),
                        fontSize = state.typeLook.statsSize.sp,
                        fontWeight = FontWeight.Medium,
                    )
                }
                Spacer(Modifier.height(16.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text("时钟大小", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "${state.typeLook.clockScalePercent}%",
                        style = MaterialTheme.typography.bodyMedium,
                        color = colors.onBackground,
                    )
                }
                Slider(
                    value = state.typeLook.clockScalePercent.toFloat(),
                    onValueChange = { value ->
                        onSetTypeLook(state.typeLook.copy(clockScalePercent = value.roundToInt()))
                    },
                    valueRange = TypeLook.CLOCK_MIN.toFloat()..TypeLook.CLOCK_MAX.toFloat(),
                    colors = SliderDefaults.colors(
                        thumbColor = colors.primary,
                        activeTrackColor = colors.primary,
                        inactiveTrackColor = colors.outline,
                    ),
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text("监控字号", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "${state.typeLook.statsSize}",
                        style = MaterialTheme.typography.bodyMedium,
                        color = colors.onBackground,
                    )
                }
                Slider(
                    value = state.typeLook.statsSize.toFloat(),
                    onValueChange = { value ->
                        onSetTypeLook(state.typeLook.copy(statsSize = value.roundToInt()))
                    },
                    valueRange = TypeLook.STATS_MIN.toFloat()..TypeLook.STATS_MAX.toFloat(),
                    colors = SliderDefaults.colors(
                        thumbColor = colors.primary,
                        activeTrackColor = colors.primary,
                        inactiveTrackColor = colors.outline,
                    ),
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text("按钮字号", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "${state.typeLook.tileSize}",
                        style = MaterialTheme.typography.bodyMedium,
                        color = colors.onBackground,
                    )
                }
                Slider(
                    value = state.typeLook.tileSize.toFloat(),
                    onValueChange = { value ->
                        onSetTypeLook(state.typeLook.copy(tileSize = value.roundToInt()))
                    },
                    valueRange = TypeLook.TILE_MIN.toFloat()..TypeLook.TILE_MAX.toFloat(),
                    colors = SliderDefaults.colors(
                        thumbColor = colors.primary,
                        activeTrackColor = colors.primary,
                        inactiveTrackColor = colors.outline,
                    ),
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text("底部字号", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "${state.typeLook.chipSize}",
                        style = MaterialTheme.typography.bodyMedium,
                        color = colors.onBackground,
                    )
                }
                Slider(
                    value = state.typeLook.chipSize.toFloat(),
                    onValueChange = { value ->
                        onSetTypeLook(state.typeLook.copy(chipSize = value.roundToInt()))
                    },
                    valueRange = TypeLook.CHIP_MIN.toFloat()..TypeLook.CHIP_MAX.toFloat(),
                    colors = SliderDefaults.colors(
                        thumbColor = colors.primary,
                        activeTrackColor = colors.primary,
                        inactiveTrackColor = colors.outline,
                    ),
                )
            }
        }
        Spacer(Modifier.height(20.dp))
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = colors.surface,
            shape = MaterialTheme.shapes.large,
        ) {
            Column(Modifier.padding(20.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text("天气", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "显示在日期右边。填城市名即可，也支持经纬度，例如 31.23,121.47。免费接口，不用密钥。",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                    Switch(
                        checked = state.weatherEnabled,
                        onCheckedChange = onSetWeatherEnabled,
                        colors = SwitchDefaults.colors(
                            checkedThumbColor = colors.onPrimary,
                            checkedTrackColor = colors.primary,
                        ),
                    )
                }
                if (state.weatherEnabled) {
                    Spacer(Modifier.height(14.dp))
                    OutlinedTextField(
                        value = weatherCity,
                        onValueChange = {
                            weatherCity = it
                            onSetWeatherCity(it)
                        },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("城市") },
                        placeholder = { Text(HubPreferences.DEFAULT_WEATHER_CITY) },
                        singleLine = true,
                        shape = MaterialTheme.shapes.small,
                        colors = fieldColors,
                    )
                    val weatherHint = state.weather?.clockLine()
                        ?: state.weatherError
                        ?: "正在获取…"
                    Spacer(Modifier.height(10.dp))
                    Text(
                        weatherHint,
                        style = MaterialTheme.typography.bodyMedium,
                        color = if (state.weatherError != null && state.weather == null) {
                            colors.error
                        } else {
                            colors.onSurfaceVariant
                        },
                    )
                }
            }
        }
        Spacer(Modifier.height(20.dp))
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = colors.surface,
            shape = MaterialTheme.shapes.large,
        ) {
            Column(Modifier.padding(20.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text("语音唤醒", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(4.dp))
                        Text(
                            if (state.wakeWordEnabled) {
                                "已开。说「岸宝」会亮屏并进入聆听，全程在手机上离线识别。"
                            } else {
                                "已关。打开后会持续听唤醒词，需要麦克风权限。"
                            },
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                    Switch(
                        checked = state.wakeWordEnabled,
                        onCheckedChange = onSetWakeWord,
                        colors = SwitchDefaults.colors(
                            checkedThumbColor = colors.onPrimary,
                            checkedTrackColor = colors.primary,
                        ),
                    )
                }
            }
        }
        Spacer(Modifier.height(20.dp))
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = colors.surface,
            shape = MaterialTheme.shapes.large,
        ) {
            Column(Modifier.padding(20.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text("电脑关机后熄屏", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(4.dp))
                        Text(
                            if (state.powerScreen) {
                                "已开。插拔电源不会熄屏。Windows 关机一段时间后会压暗，网络恢复后自动亮起。"
                            } else {
                                "已关。屏幕按系统超时处理，插拔电源也不会锁屏。"
                            },
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                    Switch(
                        checked = state.powerScreen,
                        onCheckedChange = onSetPowerScreen,
                        colors = SwitchDefaults.colors(
                            checkedThumbColor = colors.onPrimary,
                            checkedTrackColor = colors.primary,
                        ),
                    )
                }
                if (state.powerScreen) {
                    Spacer(Modifier.height(16.dp))
                    Text("电脑关机后多久熄屏", style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.height(8.dp))
                    Row(
                        modifier = Modifier.horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        listOf(30 to "30秒", 60 to "1分钟", 120 to "2分钟", 300 to "5分钟").forEach { (sec, label) ->
                            FilterChip(
                                selected = state.hubSleepDelaySec == sec,
                                onClick = { onSetHubSleepDelay(sec) },
                                label = { Text(label) },
                                colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = colors.primary.copy(alpha = 0.28f),
                                    selectedLabelColor = colors.onBackground,
                                    containerColor = colors.surfaceVariant,
                                    labelColor = colors.onSurfaceVariant,
                                ),
                            )
                        }
                    }
                    Spacer(Modifier.height(14.dp))
                    Text("离线判定间隔", style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.height(8.dp))
                    Row(
                        modifier = Modifier.horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        listOf(10 to "10秒", 15 to "15秒", 30 to "30秒", 60 to "1分钟").forEach { (sec, label) ->
                            FilterChip(
                                selected = state.hubReconnectSec == sec,
                                onClick = { onSetHubReconnect(sec) },
                                label = { Text(label) },
                                colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = colors.primary.copy(alpha = 0.28f),
                                    selectedLabelColor = colors.onBackground,
                                    containerColor = colors.surfaceVariant,
                                    labelColor = colors.onSurfaceVariant,
                                ),
                            )
                        }
                    }
                }
            }
        }
        Spacer(Modifier.height(24.dp))
    }
    }
}

private fun displayName(context: android.content.Context, uri: Uri): String? {
    val resolver = context.contentResolver
    resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
        val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
        if (index >= 0 && cursor.moveToFirst()) {
            return cursor.getString(index)?.takeIf { it.isNotBlank() }
        }
    }
    return uri.lastPathSegment?.substringAfterLast('/')
}
