package cn.weiekko.dock.data

import kotlinx.serialization.Serializable

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
    val notify: NotifyState? = null,
)

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
)

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
