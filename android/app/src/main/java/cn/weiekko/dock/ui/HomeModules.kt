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

// ---- 模块画布与防烧屏（从 HomeScreen.kt 拆出）----
/** 防烧屏三件套的当前值；编辑态全部归零/恢复，方便对位。 */
internal data class BurnShifts(
    val clock: PixelShift,
    val canvas: PixelShift,
    val dim: Float,
)

@Composable
internal fun rememberBurnShifts(active: Boolean): BurnShifts {
    val startedAt = remember { System.currentTimeMillis() }
    var elapsedMin by remember { mutableLongStateOf(0L) }
    var dim by remember { mutableFloatStateOf(1f) }
    LaunchedEffect(Unit) {
        while (true) {
            elapsedMin = (System.currentTimeMillis() - startedAt) / 60_000
            dim = BurnGuard.nightDimFactor(LocalDateTime.now())
            delay(30_000)
        }
    }
    return if (active) {
        BurnShifts(
            clock = BurnGuard.driftShift(elapsedMin),
            canvas = BurnGuard.staticShift(elapsedMin),
            dim = dim,
        )
    } else {
        BurnShifts(PixelShift(0, 0), PixelShift(0, 0), 1f)
    }
}

internal data class EditCanvas(
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
internal fun PlaceModule(
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
internal fun ModuleBox(
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
internal fun EditBar(
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
internal fun EditChip(
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
internal fun EmptySlot(label: String) {
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
internal fun rememberNow(): LocalDateTime {
    var now by remember { mutableStateOf(LocalDateTime.now()) }
    LaunchedEffect(Unit) {
        while (true) {
            now = LocalDateTime.now()
            delay(1_000)
        }
    }
    return now
}

