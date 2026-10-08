package cn.weiekko.dock.power

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import cn.weiekko.dock.MainActivity
import cn.weiekko.dock.R
import cn.weiekko.dock.data.HubClient
import cn.weiekko.dock.data.HubPreferences
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class HubWatchService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var watchJob: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        ensureChannel()
        startInForeground()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startInForeground()
        if (watchJob?.isActive != true) {
            watchJob = scope.launch { watchLoop() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        watchJob?.cancel()
        scope.cancel()
        super.onDestroy()
    }

    private suspend fun watchLoop() {
        val prefs = HubPreferences(applicationContext)
        val client = HubClient()
        while (scope.isActive) {
            val connection = prefs.connection.first()
            val hubEnabled = prefs.hubEnabled.first()
            val intervalMs = prefs.hubReconnectSec.first().coerceIn(5, 120) * 1000L
            if (!hubEnabled || !connection.isConfigured) {
                delay(intervalMs)
                continue
            }
            val ok = withContext(Dispatchers.IO) {
                runCatching { client.health(connection) }.isSuccess
            }
            if (ok) {
                startActivity(MainActivity.wakeIntent(this@HubWatchService))
                stopSelf()
                return
            }
            delay(intervalMs)
        }
    }

    private fun startInForeground() {
        val pending = PendingIntent.getActivity(
            this,
            0,
            MainActivity.wakeIntent(this),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_helm)
            .setContentTitle(getString(R.string.hub_watch_title))
            .setContentText(getString(R.string.hub_watch_text))
            .setContentIntent(pending)
            .setOngoing(true)
            .setSilent(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            ServiceCompat.startForeground(
                this,
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun ensureChannel() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                getString(R.string.hub_watch_channel),
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                setShowBadge(false)
                description = getString(R.string.hub_watch_text)
            },
        )
    }

    companion object {
        private const val CHANNEL_ID = "hub_watch"
        private const val NOTIFICATION_ID = 47

        fun start(context: Context) {
            val intent = Intent(context, HubWatchService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, HubWatchService::class.java))
        }
    }
}
