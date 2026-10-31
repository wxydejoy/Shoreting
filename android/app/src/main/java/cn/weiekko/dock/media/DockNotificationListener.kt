package cn.weiekko.dock.media

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import cn.weiekko.dock.data.HubClient
import cn.weiekko.dock.data.HubConnection
import cn.weiekko.dock.data.HubPreferences
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

/**
 * 通知镜像（F35）：白名单应用的通知送到 Hub，书桌屏幕亮一下。
 *
 * 这个服务同时干两件事：给 MediaSessionManager 放行（现在播控要用），以及
 * F35 的通知镜像——后者默认关，设置页开了开关、白名单里有包名才会送。
 * 过滤规矩都在 [MirrorFilter]（纯函数，测试直接打它）；这里只管收事件、
 * 拿当前配置、把话送到 Hub。发送失败静默——通知不值得连累服务。
 */
class DockNotificationListener : NotificationListenerService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val prefs by lazy { HubPreferences(this) }
    private val client = HubClient()

    private val mirrorEnabled = MutableStateFlow(false)
    private val mirrorApps = MutableStateFlow<Set<String>>(emptySet())
    private val connection = MutableStateFlow(HubConnection())

    override fun onCreate() {
        super.onCreate()
        scope.launch { prefs.mirrorEnabled.collect { mirrorEnabled.value = it } }
        scope.launch { prefs.mirrorApps.collect { mirrorApps.value = it.toSet() } }
        scope.launch { prefs.connection.collect { connection.value = it } }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val notification = sbn?.notification ?: return
        val text = notification.extras?.getCharSequence(Notification.EXTRA_TEXT)
        val go = MirrorFilter.shouldMirror(
            enabled = mirrorEnabled.value,
            apps = mirrorApps.value,
            packageName = sbn.packageName.orEmpty(),
            ongoing = sbn.isOngoing,
            groupSummary = notification.flags and Notification.FLAG_GROUP_SUMMARY != 0,
            text = text,
        )
        if (!go) return
        val app = sbn.packageName.orEmpty()
        val title = notification.extras?.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val body = text?.toString().orEmpty()
        scope.launch {
            val conn = connection.value
            if (!conn.isConfigured) return@launch
            kotlinx.coroutines.withContext(Dispatchers.IO) {
                runCatching { client.mirror(conn, app, title, body) }
            }
        }
    }
}
