package cn.weiekko.dock.power

import android.app.Activity
import android.app.admin.DeviceAdminReceiver
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import android.view.WindowManager

class DockDeviceAdmin : DeviceAdminReceiver()

object DockPower {
    fun adminComponent(context: Context): ComponentName =
        ComponentName(context, DockDeviceAdmin::class.java)

    fun isAdmin(context: Context): Boolean {
        val dpm = context.getSystemService(DevicePolicyManager::class.java) ?: return false
        return dpm.isAdminActive(adminComponent(context))
    }

    fun requestAdminIntent(context: Context): Intent {
        return Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN).apply {
            putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, adminComponent(context))
            putExtra(
                DevicePolicyManager.EXTRA_ADD_EXPLANATION,
                "授权后，拔掉充电会立刻锁屏熄屏。只用于锁屏，不会改密码或擦除数据。",
            )
        }
    }

    fun isPlugged(context: Context): Boolean {
        val sticky = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val plugged = sticky?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0
        return plugged != 0
    }

    fun keepScreenOn(activity: Activity, on: Boolean) {
        if (on) {
            activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            activity.window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    /** Hub 休眠：压黑背光，但不锁屏，避免 MIUI 把进程杀掉。 */
    fun dimForHubSleep(activity: Activity, dim: Boolean) {
        val params = activity.window.attributes
        params.screenBrightness = if (dim) {
            0f
        } else {
            WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
        }
        activity.window.attributes = params
    }

    /** 起床闹钟（F11）：progress 0→1，背光从近黑爬到正常，模拟日出。 */
    fun rampForAlarm(activity: Activity, progress: Float) {
        val params = activity.window.attributes
        params.screenBrightness = 0.03f + 0.97f * progress.coerceIn(0f, 1f)
        activity.window.attributes = params
    }

    fun wake(activity: Activity) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            activity.setShowWhenLocked(true)
            activity.setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            activity.window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON,
            )
        }
        keepScreenOn(activity, true)
        val pm = activity.getSystemService(PowerManager::class.java) ?: return
        @Suppress("DEPRECATION")
        val lock = pm.newWakeLock(
            PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP,
            "helm:dock-wake",
        )
        lock.acquire(3_000L)
    }

    fun sleep(activity: Activity) {
        keepScreenOn(activity, false)
        val dpm = activity.getSystemService(DevicePolicyManager::class.java) ?: return
        if (dpm.isAdminActive(adminComponent(activity))) {
            dpm.lockNow()
        }
    }
}
