package cn.weiekko.dock.data

import kotlinx.serialization.Serializable
import java.util.Locale
import kotlin.math.roundToInt

@Serializable
data class WeatherInfo(
    val city: String,
    val celsius: Double,
    val condition: String,
    val query: String = "",
    val pm25: Double? = null,  // F14：Hub 代天气时顺路捎的，直连源没有就没有
) {
    fun clockLine(): String {
        val temp = String.format(Locale.US, "%.0f°", celsius)
        return "$city  $temp  $condition"
    }

    /** 「空气 良 42」。没有 PM2.5 就 null——磁贴少一行，不空占地方。 */
    fun airLine(): String? {
        val value = pm25 ?: return null
        return "空气${pm25Grade(value)} ${value.roundToInt()}"
    }

    companion object {
        /** PM2.5 分档（μg/m³，GB 3095 日均），与 Hub 的 weather.pm25_grade 同一张表。 */
        fun pm25Grade(pm25: Double): String = when {
            pm25 < 35 -> "优"
            pm25 < 75 -> "良"
            pm25 < 115 -> "轻度污染"
            pm25 < 150 -> "中度污染"
            pm25 < 250 -> "重度污染"
            else -> "严重污染"
        }
    }
}

data class WinApp(
    val id: String,
    val name: String,
    val icon: String? = null,
    val online: Boolean = true,
    val running: Boolean = false,
)

object DemoSnapshot {
    val weather = WeatherInfo(
        city = "上海",
        celsius = 26.0,
        condition = "多云",
        pm25 = 42.0,
    )

    val winApps = listOf(
        WinApp(id = "chrome", name = "Chrome", icon = "chrome"),
        WinApp(id = "cursor", name = "Cursor", icon = "cursor"),
        WinApp(id = "terminal", name = "终端", icon = "terminal"),
    )

    val media = MediaInfo(
        online = true,
        playing = true,
        title = "Night Drive",
        artist = "Demo",
        app = "Music",
        updatedAt = "2026-08-15T16:00:00Z",
    )

    fun create(): Snapshot = Snapshot(
        protocol = 1,
        hub = HubStatus(name = "岸亭", mijia = "ok"),
        temperature = Temperature(
            id = "desk",
            name = "室内",
            celsius = 24.6,
            humidity = 48.0,
            updatedAt = "2026-08-15T16:00:00Z",
            online = true,
        ),
        pc = PcStatus(
            online = true,
            updatedAt = "2026-08-15T16:00:00Z",
            cpu = PcCpu(percent = 24.0, tempCelsius = 58.0),
            memory = PcMemory(percent = 47.0, usedGb = 15.0, totalGb = 32.0),
            gpu = PcGpu(name = "RTX 4070", percent = 12.0, tempCelsius = 49.0),
            fps = 144.0,
        ),
        media = media,
        companion = CompanionStatus(ready = true),
        devices = listOf(
            HubDevice(
                id = "lamp",
                name = "台灯",
                type = "light",
                online = true,
                on = true,
                brightness = 72,
            ),
            HubDevice(
                id = "monitor-plug",
                name = "显示器",
                type = "switch",
                online = true,
                on = false,
            ),
        ),
        scenes = listOf(
            Scene(id = "work", name = "干活"),
            Scene(id = "bedtime", name = "晚安"),
        ),
    )

    val mini = PcStatus(
        online = true,
        updatedAt = "2026-08-15T16:00:00Z",
        cpu = PcCpu(percent = 18.0),
        memory = PcMemory(percent = 41.0, usedGb = 6.6, totalGb = 16.0),
        gpu = PcGpu(name = "M4", percent = 8.0),
    )
}
