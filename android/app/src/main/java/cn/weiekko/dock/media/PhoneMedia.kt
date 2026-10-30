package cn.weiekko.dock.media

import android.content.ComponentName
import android.content.Context
import android.media.AudioManager
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.view.KeyEvent
import cn.weiekko.dock.data.MediaInfo
import java.time.Instant

class PhoneMedia(private val context: Context) {
    private val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val sessions =
        context.getSystemService(Context.MEDIA_SESSION_SERVICE) as MediaSessionManager
    private val listenerName = ComponentName(context, DockNotificationListener::class.java)
    private val keeper = MediaSnapshotKeeper()

    fun dispatch(action: String) {
        val code = when (action) {
            "toggle" -> KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE
            "next" -> KeyEvent.KEYCODE_MEDIA_NEXT
            "previous" -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
            else -> return
        }
        audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
        audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
    }

    fun snapshot(): MediaInfo = keeper.keep(read())

    private fun read(): MediaInfo {
        val now = Instant.now().toString()
        val controller = activeController()
        if (controller != null) {
            val meta = controller.metadata
            val state = controller.playbackState?.state
            val playing = when (state) {
                PlaybackState.STATE_PLAYING,
                PlaybackState.STATE_BUFFERING,
                PlaybackState.STATE_FAST_FORWARDING,
                PlaybackState.STATE_REWINDING,
                -> true
                PlaybackState.STATE_PAUSED,
                PlaybackState.STATE_STOPPED,
                PlaybackState.STATE_NONE,
                -> false
                else -> audio.isMusicActive
            }
            return MediaInfo(
                online = true,
                playing = playing,
                title = meta.string(MediaMetadata.METADATA_KEY_TITLE)
                    ?: meta.string(MediaMetadata.METADATA_KEY_DISPLAY_TITLE),
                artist = meta.string(MediaMetadata.METADATA_KEY_ARTIST)
                    ?: meta.string(MediaMetadata.METADATA_KEY_ALBUM_ARTIST),
                app = appLabel(controller.packageName),
                updatedAt = now,
            )
        }
        return MediaInfo(
            online = true,
            playing = audio.isMusicActive,
            title = null,
            artist = null,
            app = "手机",
            updatedAt = now,
        )
    }

    private fun activeController(): MediaController? {
        val list = try {
            sessions.getActiveSessions(listenerName)
        } catch (_: SecurityException) {
            return null
        }
        return list.firstOrNull { it.playbackState?.state == PlaybackState.STATE_PLAYING }
            ?: list.firstOrNull()
    }

    private fun appLabel(packageName: String): String {
        return try {
            val pm = context.packageManager
            val info = pm.getApplicationInfo(packageName, 0)
            pm.getApplicationLabel(info).toString()
        } catch (_: Exception) {
            "手机"
        }
    }
}

private fun MediaMetadata?.string(key: String): String? =
    this?.getString(key)?.trim()?.takeIf { it.isNotEmpty() }
