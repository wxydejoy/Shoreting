package cn.weiekko.dock.background

import android.content.Context
import android.net.Uri
import android.util.Log
import cn.weiekko.dock.data.HubConnection
import cn.weiekko.dock.data.HubPreferences
import cn.weiekko.dock.data.DockLayout
import cn.weiekko.dock.data.ModuleCanvas
import cn.weiekko.dock.data.ModuleCatalog
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import java.io.File
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.min
import kotlin.math.roundToInt

class BackgroundHost(private val context: Context) {
    val library = BackgroundLibrary(backgroundDir(context))
    private val prefs = HubPreferences(context)
    private val token = AtomicReference(HubConnection.DEFAULT_TOKEN)
    private val current = AtomicReference<String?>(null)
    private val userLayout = AtomicReference(DockLayout())
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
        scope.launch {
            prefs.layout.collect { userLayout.set(it) }
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
                layoutJson = ::layoutSnapshotJson,
                port = BackgroundServer.PORT,
            )
        } catch (error: Exception) {
            Log.e(TAG, "background page failed", error)
        }
    }

    /**
     * 网页布局编辑器要的快照。默认布局里的绝对常量（磁贴 84、CPU 卡 340/110、
     * 角标 210/56、间距 10）都是 dp，HomeScreen 传给 ModuleCanvas 的是 dp 画布
     * （padding 后的 maxWidth/maxHeight）——这里必须用同一口径，不然网页算出的
     * 默认位置和手机上差一个 density。存布局存的是 0..1 的比例，编辑器也只提交
     * 用户真正动过的槽位，没动过的槽位留空，手机端仍用自己的默认值。
     */
    private fun layoutSnapshotJson(): String? {
        val metrics = context.resources.displayMetrics
        if (metrics.widthPixels <= 0 || metrics.heightPixels <= 0) return null
        val density = metrics.density.coerceAtLeast(1f)
        val canvasW = metrics.widthPixels / density - 20f * 2f
        val canvasH = metrics.heightPixels / density - 10f * 2f
        if (canvasW <= 0f || canvasH <= 0f) return null
        val tileH = min(canvasH * 0.22f, 84f)
        val tileW = min(canvasW * 0.20f, tileH * 1.7f)
        val canvas = ModuleCanvas(canvasW, canvasH, tileW, tileH)
        val defaults = DockLayout(modules = ModuleCatalog.defaultRects(canvas))
        val merged = userLayout.get().mergedWith(defaults)
        return buildString {
            append("{\"ok\":true,\"canvas\":{\"w\":")
            append(canvasW.roundToInt())
            append(",\"h\":")
            append(canvasH.roundToInt())
            append("},\"slots\":[")
            ModuleCatalog.all.forEachIndexed { index, slot ->
                if (index > 0) append(',')
                append("{\"id\":")
                append(jsonString(slot.id))
                append(",\"label\":")
                append(jsonString(slot.label))
                append('}')
            }
            append("],\"defaults\":")
            append(LAYOUT_JSON.encodeToString(DockLayout.serializer(), defaults))
            append(",\"modules\":")
            append(LAYOUT_JSON.encodeToString(DockLayout.serializer(), merged))
            append('}')
        }
    }

    private fun jsonString(raw: String): String {
        val out = StringBuilder("\"")
        raw.forEach { ch ->
            when (ch) {
                '"' -> out.append("\\\"")
                '\\' -> out.append("\\\\")
                '\n' -> out.append("\\n")
                '\r' -> out.append("\\r")
                '\t' -> out.append("\\t")
                else -> if (ch < ' ') out.append("\\u%04x".format(ch.code)) else out.append(ch)
            }
        }
        return out.append('"').toString()
    }

    companion object {
        private const val TAG = "BackgroundHost"

        private val LAYOUT_JSON = Json {
            ignoreUnknownKeys = true
            encodeDefaults = true
        }

        fun backgroundDir(context: Context): File {
            val parent = context.getExternalFilesDir(null) ?: context.filesDir
            return File(parent, "backgrounds").apply { mkdirs() }
        }
    }
}
