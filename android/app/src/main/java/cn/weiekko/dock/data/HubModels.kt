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
)

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
