package cn.weiekko.dock.data

/**
 * 磁贴的两个坐标：[ModuleType] 说「这是什么种类」，[ModuleSlot] 说「具体摆哪一个」。
 * 布局 Map 的键就是槽位的 id——沿用老枚举名（"Clock"、"Win0"…），旧布局不迁移照常读。
 */
enum class ModuleType {
    Clock,
    Pc,
    Win,
    Device,
    Media,
    Weather,
    Indoor,
    ;

    /** 多实例的种类（Win/Device）可以按序号摆好几个；单例种类只有一个。 */
    val multiInstance: Boolean
        get() = this == Win || this == Device
}

/** 一个可摆的磁贴槽位：[id] 是布局存储键，[index] 是多实例种类里的序号（单例为 -1）。 */
data class ModuleSlot(
    val id: String,
    val type: ModuleType,
    val label: String,
    val index: Int = -1,
    /** 没有用户布局时的默认位置；每个槽位自己算自己的，新磁贴登记一处即全套生效。 */
    val defaultRect: ((ModuleCanvas) -> ModuleRect)? = null,
)

/** 默认布局的画布：主屏把画布尺寸给进来，槽位按同一套基准摆位。 */
data class ModuleCanvas(
    val canvasW: Float,
    val canvasH: Float,
    val tileW: Float,
    val tileH: Float,
) {
    val gap = 10f
    val lift = -canvasH * 0.04f
    val colH = tileH * 3f + gap * 2f
    val leftY = ((canvasH - colH) / 2f + lift * 0.25f).coerceAtLeast(0f)
    val rightX = (canvasW - tileW).coerceAtLeast(0f)
    val chipW = (canvasW * 0.26f).coerceAtMost(210f)
    val chipH = (canvasH * 0.15f).coerceAtMost(56f)
    val chipY = (canvasH - chipH).coerceAtLeast(0f)
    val weatherX = (canvasW / 2f - chipW - 6f).coerceAtLeast(0f)
    val indoorX = (canvasW / 2f + 6f).coerceAtMost(canvasW - chipW)

    /** 绝对像素坐标 → 0..1 的布局矩形。 */
    fun rect(x: Float, y: Float, w: Float, h: Float) = ModuleRect(
        x = (x / canvasW).coerceIn(0f, 1f),
        y = (y / canvasH).coerceIn(0f, 1f),
        w = (w / canvasW).coerceIn(0.08f, 1f),
        h = (h / canvasH).coerceIn(0.08f, 1f),
    )
}

/**
 * 全部磁贴槽位。加一类新磁贴只动两处：这里登记槽位（含默认位置），
 * 再在 HomeScreen 的渲染分支里画自己——设置页开关、编辑态、默认布局都跟着目录走。
 */
object ModuleCatalog {

    val all: List<ModuleSlot> = listOf(
        ModuleSlot(
            id = "Clock",
            type = ModuleType.Clock,
            label = "时钟",
            defaultRect = { c ->
                val clockW = (c.canvasW * 0.44f).coerceAtMost(c.canvasH * 0.95f)
                val clockH = (c.canvasH * 0.44f).coerceAtMost(clockW * 0.78f)
                val clockX = (c.canvasW - clockW) / 2f
                val clockY = (c.canvasH - clockH) / 2f + c.lift
                c.rect(clockX, clockY, clockW, clockH)
            },
        ),
        ModuleSlot(
            id = "Pc",
            type = ModuleType.Pc,
            label = "CPU 信息",
            defaultRect = { c ->
                val pcW = (c.canvasW * 0.40f).coerceAtMost(340f)
                val pcH = (c.canvasH * 0.26f).coerceAtMost(110f)
                val pcX = (c.canvasW - pcW) / 2f
                c.rect(pcX, 2f, pcW, pcH)
            },
        ),
        ModuleSlot(
            id = "Win0",
            type = ModuleType.Win,
            label = "启动 1",
            index = 0,
            defaultRect = { c -> c.rect(0f, c.leftY, c.tileW, c.tileH) },
        ),
        ModuleSlot(
            id = "Win1",
            type = ModuleType.Win,
            label = "启动 2",
            index = 1,
            defaultRect = { c -> c.rect(0f, c.leftY + c.tileH + c.gap, c.tileW, c.tileH) },
        ),
        ModuleSlot(
            id = "Win2",
            type = ModuleType.Win,
            label = "启动 3",
            index = 2,
            defaultRect = { c -> c.rect(0f, c.leftY + (c.tileH + c.gap) * 2f, c.tileW, c.tileH) },
        ),
        ModuleSlot(
            id = "Device0",
            type = ModuleType.Device,
            label = "设备 1",
            index = 0,
            defaultRect = { c -> c.rect(c.rightX, c.leftY, c.tileW, c.tileH) },
        ),
        ModuleSlot(
            id = "Device1",
            type = ModuleType.Device,
            label = "设备 2",
            index = 1,
            defaultRect = { c -> c.rect(c.rightX, c.leftY + c.tileH + c.gap, c.tileW, c.tileH) },
        ),
        ModuleSlot(
            id = "Media",
            type = ModuleType.Media,
            label = "媒体",
            defaultRect = { c -> c.rect(c.rightX, c.leftY + (c.tileH + c.gap) * 2f, c.tileW, c.tileH) },
        ),
        ModuleSlot(
            id = "Weather",
            type = ModuleType.Weather,
            label = "室外天气",
            defaultRect = { c -> c.rect(c.weatherX, c.chipY, c.chipW, c.chipH) },
        ),
        ModuleSlot(
            id = "Indoor",
            type = ModuleType.Indoor,
            label = "室内温湿度",
            defaultRect = { c -> c.rect(c.indoorX, c.chipY, c.chipW, c.chipH) },
        ),
    )

    private val byId: Map<String, ModuleSlot> = all.associateBy { it.id }

    fun slot(id: String): ModuleSlot? = byId[id]

    fun ofType(type: ModuleType): List<ModuleSlot> = all.filter { it.type == type }

    fun defaultRects(canvas: ModuleCanvas): Map<String, ModuleRect> =
        all.mapNotNull { slot ->
            slot.defaultRect?.let { fn -> slot.id to fn(canvas) }
        }.toMap()
}
