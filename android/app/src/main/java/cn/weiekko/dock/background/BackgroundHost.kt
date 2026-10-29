package cn.weiekko.dock.background

import android.content.Context
import android.net.Uri
import android.util.Log
import cn.weiekko.dock.data.HubConnection
import cn.weiekko.dock.data.HubPreferences
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.io.File
import java.util.concurrent.atomic.AtomicReference

class BackgroundHost(private val context: Context) {
    val library = BackgroundLibrary(backgroundDir(context))
    private val prefs = HubPreferences(context)
    private val token = AtomicReference(HubConnection.DEFAULT_TOKEN)
    private val current = AtomicReference<String?>(null)
    private val gate = BackgroundGate { token.get() }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var server: BackgroundServer? = null
    val pageUrl: String?
        get() {
            val address = lanIpv4() ?: return null
            return "http://$address:${BackgroundServer.PORT}"
        }

    fun start() {
        scope.launch {
            prefs.connection.collect { token.set(it.token) }
        }
        scope.launch {
            prefs.videoUri.collect { stored -> current.set(library.currentId(stored)) }
        }
        try {
            server = BackgroundServer(
                library = library,
                gate = gate,
                pageHtml = BACKGROUND_PAGE,
                currentId = { current.get() },
                onCurrent = { id ->
                    current.set(id)
                    scope.launch {
                        val stored = id?.let { name ->
                            library.file(name)?.let { file -> Uri.fromFile(file).toString() }
                        } ?: BackgroundLibrary.OFF
                        prefs.saveVideoUri(stored)
                    }
                },
                port = BackgroundServer.PORT,
            )
        } catch (error: Exception) {
            Log.e(TAG, "background page failed", error)
        }
    }

    companion object {
        private const val TAG = "BackgroundHost"

        fun backgroundDir(context: Context): File {
            val parent = context.getExternalFilesDir(null) ?: context.filesDir
            return File(parent, "backgrounds").apply { mkdirs() }
        }
    }
}
