package cn.weiekko.dock.data

/**
 * F25 在场上报：把「手机在不在家」用最少的请求告诉 Hub。
 *
 * 两个入口：
 * - [onWifiChanged] 给系统的 ConnectivityManager 回调（Wi-Fi 一断就有，不吃轮询节奏）；
 * - [onPoll] 挂在轮询循环上，顺手补采样 + 在 Hub 答得上的那一刻发请求。
 *
 * 三条规矩：
 * - **离家要够久**：Wi-Fi 断开满 [AWAY_AFTER_MS] 才算 away。门口取个快递的
 *   工夫不该换来一句「回来了」。
 * - **Hub 连不上 ≠ 离家**：Wi-Fi 一直在、只是 snapshot 拿不到（Hub 重启），
 *   `wifiLostAt` 一直是空，回来只补 home，不会凭空问候。
 * - **away 发不出去就捎回来**：人不在家时 Hub 大概率连不上，这条注定失败；
 *   回家连上后先补 `away` 再报 `home`，Hub 那边照样有 away→home 的边沿。
 *
 * 时间统一用调用方传入的 `nowMs`（生产用 `SystemClock.elapsedRealtime()`），
 * 测试里可以手动拨钟。
 */
class PresenceReporter(
    /** 发一条状态；返回是否送达（网络失败 / 没配置 Hub 都是 false）。 */
    private val send: suspend (state: String) -> Boolean,
) {
    /** 最近一次成功送达的状态；"" 表示还没报过（Hub 侧是 unknown）。 */
    var reported: String = ""
        private set

    /** Wi-Fi 是从哪一刻断的；连着 Wi-Fi 时一直是 null。 */
    private var wifiLostAt: Long? = null

    /** 最近一次成功上报的时刻；心跳用。 */
    private var lastSentAt = 0L

    /**
     * 网络回调喂的状态变化。只记「断」的时刻——「恢复」不清状态：清要等
     * [onPoll] 评判完再做，不然重连那一轮会把「断了多久」的证据冲掉。
     */
    fun onWifiChanged(onWifi: Boolean, nowMs: Long) {
        if (!onWifi && wifiLostAt == null) wifiLostAt = nowMs
    }

    /**
     * 每轮轮询调一次。`reachable` 以「Hub 真的答上话」为准——401 / 500 也算
     * 连得上（Hub 在，只是这一口没答上），网络断才是 false。
     */
    suspend fun onPoll(onWifi: Boolean, reachable: Boolean, nowMs: Long) {
        // 没有网络回调时（或漏了一次），轮询顺手补采样
        onWifiChanged(onWifi, nowMs)
        if (!reachable) return
        val lostFor = wifiLostAt?.let { nowMs - it }
        if (!onWifi) {
            // 连得上 Hub 却不走 Wi-Fi（VPN 之类）：按不在家算，只在边沿报
            if (reported == HOME && lostFor != null && lostFor >= AWAY_AFTER_MS) {
                report(AWAY, nowMs)
            }
            return
        }
        if (reported == HOME && lostFor != null && lostFor >= AWAY_AFTER_MS) {
            // 人离家够久了，Hub 侧还停在 home：先把 away 补上，下面的 home
            // 才能踩出 away→home 的边沿
            report(AWAY, nowMs)
        }
        if (reported != HOME || nowMs - lastSentAt >= HEARTBEAT_MS) {
            report(HOME, nowMs)
        }
        wifiLostAt = null // 回到 Wi-Fi：评判完这一轮才清，别把断开的时刻弄丢
    }

    private suspend fun report(state: String, nowMs: Long) {
        if (send(state)) {
            reported = state
            lastSentAt = nowMs
        }
    }

    companion object {
        const val HOME = "home"
        const val AWAY = "away"

        /** Wi-Fi 断开多久才算「出门了」。 */
        const val AWAY_AFTER_MS = 10 * 60_000L

        /** 在家时补发 home 的间隔：Hub 重启 / 哪次上报丢了能自己长回来。 */
        const val HEARTBEAT_MS = 15 * 60_000L
    }
}
