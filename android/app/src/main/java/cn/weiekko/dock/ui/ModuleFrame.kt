package cn.weiekko.dock.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import cn.weiekko.dock.R
import cn.weiekko.dock.data.DockLayout
import cn.weiekko.dock.data.ModuleRect
import cn.weiekko.dock.data.ModuleSlot
import kotlinx.coroutines.delay
import java.time.LocalDateTime
import kotlin.math.roundToInt

/** 主屏统一的一层文字投影：白字压深底，影子把字定住。 */
internal val StatShadow = Shadow(color = Color.Black.copy(alpha = 0.95f), offset = Offset(0f, 2f), blurRadius = 22f)

/** 编辑态在主屏和磁贴之间递的那份上下文：布局、画布、选中与四个手势回调。 */
internal data class EditCanvas(
    val layout: DockLayout,
    val canvasW: Float,
    val canvasH: Float,
    val tileW: Float,
    val tileH: Float,
    val editing: Boolean,
    val selected: String?,
    val density: Density,
    val onSelect: (String?) -> Unit,
    val onMove: (String, Float, Float, Float, Float, Float, Float) -> Unit,
    val onResize: (String, Float, Float, Float, Float, Float, Float) -> Unit,
    val onHide: (String, Float, Float, Float, Float) -> Unit,
    val onToggleChrome: (String, Float, Float, Float, Float) -> Unit,
)

/** 把一个槽位按布局摆进画布；没有矩形的槽位（不该发生）直接不摆。 */
@Composable
internal fun PlaceModule(
    slot: ModuleSlot,
    canvas: EditCanvas,
    content: @Composable BoxScope.() -> Unit,
) {
    val rect = canvas.layout.rect(slot.id) ?: return
    val density = canvas.density
    ModuleBox(
        rect = rect,
        canvasW = canvas.canvasW,
        canvasH = canvas.canvasH,
        editing = canvas.editing,
        selected = canvas.selected == slot.id,
        onSelect = { canvas.onSelect(slot.id) },
        onMove = { dx, dy ->
            canvas.onMove(
                slot.id,
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
                slot.id,
                with(density) { dw.toDp().value },
                with(density) { dh.toDp().value },
                canvas.canvasW,
                canvas.canvasH,
                canvas.tileW,
                canvas.tileH,
            )
        },
        onHide = {
            canvas.onHide(slot.id, canvas.canvasW, canvas.canvasH, canvas.tileW, canvas.tileH)
        },
        onToggleChrome = {
            canvas.onToggleChrome(slot.id, canvas.canvasW, canvas.canvasH, canvas.tileW, canvas.tileH)
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
