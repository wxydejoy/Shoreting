package cn.weiekko.dock.relay

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import cn.weiekko.dock.MainActivity
import cn.weiekko.dock.R
import cn.weiekko.dock.data.NotifyItem

/**
 * 递话通知（F33/F4）：Hub 的 snapshot.notify 里有新 id 就响一下。
 *
 * 人不在书桌时这是一句「饭好了」唯一能到你的路径，所以通道用 DEFAULT 重要级，
 * 带声音；内容短命（24 小时过期），没有点击后的深链需求——打开 App 就行。
 */
object RelayNotifier {
    private const val CHANNEL_ID = "relay"
    private const val BASE_NOTIFICATION_ID = 4800

    fun consume(context: Context, items: List<NotifyItem>) {
        if (items.isEmpty()) return
        ensureChannel(context)
        val manager = NotificationManagerCompat.from(context)
        // Android 13 起通知要运行时授权；没授权时 post 只是静默失败，不该炸
        if (!manager.areNotificationsEnabled()) return
        for (item in items) {
            val notification = NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_helm)
                .setContentTitle(context.getString(R.string.relay_title))
                .setContentText(item.text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(item.text))
                .setContentIntent(openApp(context))
                .setAutoCancel(true)
                .setCategory(NotificationCompat.CATEGORY_MESSAGE)
                .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                .build()
            runCatching { manager.notify(BASE_NOTIFICATION_ID + (item.id % 1000), notification) }
        }
    }

    private fun openApp(context: Context): PendingIntent =
        PendingIntent.getActivity(
            context,
            1,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

    private fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.relay_channel),
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply {
                description = context.getString(R.string.relay_channel_desc)
            },
        )
    }
}
