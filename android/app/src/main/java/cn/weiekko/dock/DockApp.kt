package cn.weiekko.dock

import android.app.Application
import android.util.Log
import cn.weiekko.dock.background.BackgroundHost

class DockApp : Application() {
    lateinit var backgrounds: BackgroundHost
        private set

    override fun onCreate() {
        super.onCreate()
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            Log.e("DockApp", "uncaught exception in " + t.name, e)
            previous?.uncaughtException(t, e)
        }
        backgrounds = BackgroundHost(this)
        backgrounds.start()
    }
}
