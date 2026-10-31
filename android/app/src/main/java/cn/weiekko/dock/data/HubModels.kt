package cn.weiekko.dock.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName

@Serializable
data class Health(
    val ok: Boolean,
    val service: String,
    val protocol: Int,
    val name: String,
)

@Serializable
data class Snapshot(
    val protocol: Int,
    val hub: HubStatus,
    val temperature: Temperature? = null,
    val pc: PcStatus? = null,
    val media: MediaInfo? = null,
    val companion: CompanionStatus? = null,
    val devices: List<HubDevice> = emptyList(),
    /** 场景（F7）：hub.yaml 里配好的一组开关，主屏摆成按钮。 */
    val scenes: List<Scene> = emptyList(),
    val notify: NotifyState? = null,
    val mirror: MirrorState? = null,
    /** 访客模式（F26）：开着的时候对话记录都收起来了，屏上留一个小标记。 */
    val guest: GuestState? = null,
    val alarm: AlarmStatus? = null,
    val theme: ThemeStatus? = null,
    val photos: PhotoIds? = null,
    val wallpaper: WallpaperStatus? = null,
    /** 控制台改的手机端外观/电源/天气；rev 没变就不用管。 */
    val phone: PhoneSettings? = null,
    /** Hub 的状态版本号；旧版 Hub 没有这个字段，就用不了长轮询。 */
    val seq: Int? = null,
)

/** hub.yaml 里的一组开关（F7）；点一下就是一次 [SceneRun]。 */
@Serializable
data class Scene(
    val id: String,
    val name: String,
)

@Serializable
data class SceneAction(
    val id: String,
    val on: Boolean? = null,
    val brightness: Int? = null,
    val ok: Boolean = true,
    val code: String? = null,
    val message: String? = null,
)

@Serializable
data class SceneRun(
    val id: String,
    val name: String,
    val ok: Boolean,
    val said: Boolean = false,
    val actions: List<SceneAction> = emptyList(),
)

/** 一句话壁纸（F39）：今天那句，画在背景上，淡。 */
@Serializable
data class WallpaperStatus(
    val text: String,
    val date: String = "",
)

/** 回忆相框（F37）：Hub 的照片名单，配置旁有 photos/ 目录才有。 */
@Serializable
data class PhotoIds(
    val ids: List<String> = emptyList(),
)

/**
 * 手机端外观 / 电源 / 天气（控制台「三端配置 · 安卓」改的就是这一段）。
 *
 * 这些值本身存在 DataStore 里，只在手机设置页改；Hub 存一份权威副本，挂在
 * snapshot.phone 下发。**只有 [rev] 比本地记着的更大时才套用**——否则手机
 * 上刚手动调过的字号，会被下一次 3 秒轮询原样顶回去。
 */
@Serializable
data class PhoneTile(
    val style: String = "ink",
    val opacity: Int = 42,
    val corner: Int = 20,
)

@Serializable
data class PhoneType(
    val font: String = "system",
    val clock: Int = 88,
    val stats: Int = 20,
    val tile: Int = 15,
    val chip: Int = 15,
)

@Serializable
data class PhoneScreen(
    val keepOn: Boolean = true,
    val sleepSec: Int = 60,
)

@Serializable
data class PhoneWeather(
    val enabled: Boolean = true,
    val city: String = "上海",
)

/** 主屏布局的一个槽位：0..1 的画布比例。w/h < 0.05 表示占位（位置用默认值）。 */
@Serializable
data class PhoneLayoutRect(
    val x: Float = 0f,
    val y: Float = 0f,
    val w: Float = 0f,
    val h: Float = 0f,
    val visible: Boolean = true,
    val chrome: Boolean = true,
)

/** 主屏布局。rev 与外观的 rev 各走各的：存布局不该顺带把字号也顶回去。 */
@Serializable
data class PhoneLayout(
    val rev: Int = 0,
    val modules: Map<String, PhoneLayoutRect> = emptyMap(),
)

@Serializable
data class PhoneSettings(
    val rev: Int = 0,
    val tile: PhoneTile = PhoneTile(),
    val type: PhoneType = PhoneType(),
    val screen: PhoneScreen = PhoneScreen(),
    val weather: PhoneWeather = PhoneWeather(),
    val layout: PhoneLayout? = null,
)

/** 今天是什么日子（F36）：Hub 管日期（生日/节日/考试周），天气归手机自己。 */
@Serializable
data class ThemeStatus(
    val occasions: List<String> = emptyList(),
)

/** 起床闹钟（F11）：爬坡进行中的剩余/总秒数，屏幕跟着爬亮；不在爬就没有。 */
@Serializable
data class AlarmStatus(
    @SerialName("seconds_left") val secondsLeft: Double = 0.0,
    @SerialName("total_sec") val totalSec: Double = 0.0,
) {
    /** 0→1 的爬亮进度；数据不对就 null，屏幕不动。 */
    val progress: Float?
        get() = if (totalSec > 0.0) {
            (1.0 - secondsLeft / totalSec).toFloat().coerceIn(0f, 1f)
        } else {
            null
        }
}

/** 待递到手机的话（F33/F4）：安卓按自增 id 去重，见新的就发本地通知。 */
@Serializable
data class NotifyState(
    val lastId: Int = 0,
    val items: List<NotifyItem> = emptyList(),
)

@Serializable
data class NotifyItem(
    val id: Int,
    val text: String,
    val source: String = "desk",
    val at: String = "",
)

/** 通知镜像（F35）：白名单应用的通知名单，安卓拿自增 id 去重，见新的就亮一下。 */
@Serializable
data class MirrorState(
    val lastId: Int = 0,
    val items: List<MirrorItem> = emptyList(),
)

/** 访客模式（F26）：Hub 一句话开、一句话关、到点自己恢复；这里只管亮个标记。 */
@Serializable
data class GuestState(
    val since: String = "",
    val until: String? = null,
    val hours: Double = 0.0,
)

@Serializable
data class MirrorItem(
    val id: Int,
    val app: String,
    val title: String = "",
    val text: String,
    val at: String = "",
)

@Serializable
data class HubStatus(
    val name: String,
    val mijia: String,
    val message: String? = null,
)

@Serializable
data class Temperature(
    val id: String,
    val name: String,
    val celsius: Double,
    val humidity: Double? = null,
    val updatedAt: String,
    val online: Boolean,
)

@Serializable
data class PcStatus(
    val online: Boolean,
    val updatedAt: String,
    val cpu: PcCpu? = null,
    val memory: PcMemory? = null,
    val gpu: PcGpu? = null,
    val fps: Double? = null,
) {
    // updatedAt 是采样时刻：每次轮询都会前进，但画面可能一模一样（CPU 还是 12%）。
    // 让它参与相等性判断，DockUiState 就每 2～3 秒「变」一次，Compose 只能整屏重组。
    override fun equals(other: Any?): Boolean = other is PcStatus &&
        online == other.online &&
        cpu == other.cpu &&
        memory == other.memory &&
        gpu == other.gpu &&
        fps == other.fps

    override fun hashCode(): Int {
        var result = online.hashCode()
        result = 31 * result + (cpu?.hashCode() ?: 0)
        result = 31 * result + (memory?.hashCode() ?: 0)
        result = 31 * result + (gpu?.hashCode() ?: 0)
        result = 31 * result + (fps?.hashCode() ?: 0)
        return result
    }
}

@Serializable
data class PcCpu(
    val percent: Double,
    val tempCelsius: Double? = null,
)

@Serializable
data class PcMemory(
    val percent: Double,
    val usedGb: Double? = null,
    val totalGb: Double? = null,
)

@Serializable
data class PcGpu(
    val name: String? = null,
    val percent: Double? = null,
    val tempCelsius: Double? = null,
    val vramPercent: Double? = null,
    val vramUsedGb: Double? = null,
    val vramTotalGb: Double? = null,
)

@Serializable
data class MediaInfo(
    val online: Boolean,
    val playing: Boolean = false,
    val title: String? = null,
    val artist: String? = null,
    val app: String? = null,
    val updatedAt: String,
) {
    // 同 PcStatus：updatedAt 每次采样都前进，内容没变就不该算「新状态」。
    override fun equals(other: Any?): Boolean = other is MediaInfo &&
        online == other.online &&
        playing == other.playing &&
        title == other.title &&
        artist == other.artist &&
        app == other.app

    override fun hashCode(): Int {
        var result = online.hashCode()
        result = 31 * result + playing.hashCode()
        result = 31 * result + (title?.hashCode() ?: 0)
        result = 31 * result + (artist?.hashCode() ?: 0)
        result = 31 * result + (app?.hashCode() ?: 0)
        return result
    }

    companion object {
        fun phoneIdle(): MediaInfo = MediaInfo(
            online = true,
            playing = false,
            app = "手机",
            updatedAt = "",
        )
    }
}

@Serializable
data class MiniSnapshot(
    val protocol: Int = 1,
    val service: String? = null,
    val pc: PcStatus? = null,
)

@Serializable
data class CompanionStatus(
    val ready: Boolean,
    val voice: Boolean = false,
    val speaking: Boolean = false,
    val timer: TimerStatus? = null,
)

/** 番茄钟 / 正计时（F9）：倒计时带剩余秒，正计时带已计秒。 */
@Serializable
data class TimerStatus(
    val mode: String,
    val label: String = "计时",
    val totalSec: Int = 0,
    val secondsLeft: Int = 0,
    val seconds: Int = 0,
) {
    val isStopwatch: Boolean get() = mode == "stopwatch"
}

@Serializable
data class CompanionChatRequest(
    val text: String,
    val turnId: String? = null,
)

@Serializable
data class CompanionChatResponse(
    val text: String,
    val audioId: String? = null,
)

/** 她记得的事（F28）：条目可见、逐条可删，key 是删除句柄。 */
@Serializable
data class MemoryEntry(
    val key: String,
    val value: String,
    val source: String = "chat",
    val learnedAt: String = "",
)

@Serializable
data class MemoryList(
    val limit: Int = 60,
    val items: List<MemoryEntry> = emptyList(),
)

@Serializable
data class HubDevice(
    val id: String,
    val name: String,
    val type: String,
    val online: Boolean,
    val on: Boolean = false,
    val brightness: Int? = null,
    val icon: String? = null,
    val lastRunAt: String? = null,
)

@Serializable
data class DeviceCommand(
    val on: Boolean? = null,
    val brightness: Int? = null,
    val run: Boolean? = null,
)

@Serializable
data class ErrorBody(
    val error: ErrorDetail,
)

@Serializable
data class ErrorDetail(
    val code: String,
    val message: String,
)

enum class DeviceType {
    Switch,
    Light,
    Action,
    Unknown,
}

fun HubDevice.deviceType(): DeviceType = when (type) {
    "switch" -> DeviceType.Switch
    "light" -> DeviceType.Light
    "action" -> DeviceType.Action
    else -> DeviceType.Unknown
}

fun HubDevice.toWinApp(): WinApp = WinApp(
    id = id,
    name = name,
    icon = icon,
    online = online,
    running = on,
)

fun HubStatus.isLoginRequired(): Boolean = mijia == "login_required"
fun HubStatus.isOk(): Boolean = mijia == "ok"
