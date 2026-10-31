package cn.weiekko.dock.data

import kotlinx.serialization.Serializable
import kotlin.math.roundToInt

@Serializable
data class ModuleRect(
    val x: Float,
    val y: Float,
    val w: Float,
    val h: Float,
    val visible: Boolean = true,
    val chrome: Boolean = true,
) {
    fun isPlaceholder(): Boolean = w < 0.05f || h < 0.05f

    fun clamped(): ModuleRect {
        val nw = w.coerceIn(0.08f, 1f)
        val nh = h.coerceIn(0.08f, 1f)
        return copy(
            x = x.coerceIn(0f, 1f - nw),
            y = y.coerceIn(0f, 1f - nh),
            w = nw,
            h = nh,
        )
    }
}

@Serializable
data class DockLayout(
    val version: Int = 1,
    val modules: Map<String, ModuleRect> = emptyMap(),
) {
    fun rect(id: String): ModuleRect? = modules[id]

    fun with(id: String, rect: ModuleRect): DockLayout {
        val stored = if (rect.isPlaceholder()) {
            rect.copy(x = 0f, y = 0f, w = 0f, h = 0f)
        } else {
            rect.clamped()
        }
        return copy(modules = modules + (id to stored))
    }

    fun mergedWith(defaults: DockLayout): DockLayout {
        if (modules.isEmpty()) return defaults
        val merged = defaults.modules.toMutableMap()
        modules.forEach { (key, rect) ->
            val base = merged[key]
            merged[key] = if (base != null && rect.isPlaceholder()) {
                base.copy(visible = rect.visible, chrome = rect.chrome)
            } else {
                rect.clamped()
            }
        }
        return copy(version = 1, modules = merged)
    }

    companion object {
        const val GRID_PX = 8f
    }
}

/** 默认布局不再是手写的一张表：槽位目录里每个磁贴自带位置，折叠一遍就是。 */
fun defaultDockLayout(canvasW: Float, canvasH: Float, tileW: Float, tileH: Float): DockLayout =
    DockLayout(modules = ModuleCatalog.defaultRects(ModuleCanvas(canvasW, canvasH, tileW, tileH)))

fun ModuleRect.nudge(dxFrac: Float, dyFrac: Float): ModuleRect =
    copy(x = x + dxFrac, y = y + dyFrac).clamped()

fun ModuleRect.grow(dwFrac: Float, dhFrac: Float): ModuleRect =
    copy(w = w + dwFrac, h = h + dhFrac).clamped()

fun ModuleRect.snap(canvasW: Float, canvasH: Float, grid: Float = DockLayout.GRID_PX): ModuleRect {
    fun snapValue(frac: Float, total: Float): Float {
        val px = frac * total
        return ((px / grid).roundToInt() * grid) / total
    }
    return copy(
        x = snapValue(x, canvasW),
        y = snapValue(y, canvasH),
        w = snapValue(w, canvasW).coerceAtLeast(0.08f),
        h = snapValue(h, canvasH).coerceAtLeast(0.08f),
    ).clamped()
}
