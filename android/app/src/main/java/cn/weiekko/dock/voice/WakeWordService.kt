package cn.weiekko.dock.voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import cn.weiekko.dock.MainActivity
import cn.weiekko.dock.R
import com.k2fsa.sherpa.onnx.KeywordSpotter
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineRecognizer
import com.k2fsa.sherpa.onnx.OnlineRecognizerConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import com.k2fsa.sherpa.onnx.OnlineZipformer2CtcModelConfig
import com.k2fsa.sherpa.onnx.getFeatureConfig
import kotlin.concurrent.thread

class WakeWordService : Service() {
    @Volatile
    private var running = false
    @Volatile
    private var capturing = false
    private var recordThread: Thread? = null
    private var kws: KeywordSpotter? = null
    private var kwsStream: OnlineStream? = null
    private var asr: OnlineRecognizer? = null
    private var asrStream: OnlineStream? = null
    private var audioRecord: AudioRecord? = null
    @Volatile
    private var currentTurn = ""
    @Volatile
    private var chimePending = false
    private var turnAt = 0L
    private var asrAt = 0L
    private var loggedPartial = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        ensureChannel()
        startInForeground()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startInForeground()
        if (!running) startLoop()
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        capturing = false
        recordThread?.join(400)
        releaseMic()
        kwsStream?.release()
        kwsStream = null
        asrStream?.release()
        asrStream = null
        kws?.release()
        kws = null
        asr?.release()
        asr = null
        super.onDestroy()
    }

    private fun startLoop() {
        if (ContextCompat.checkSelfPermission(this, android.Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            stopSelf()
            return
        }
        running = true
        recordThread = thread(name = "wake-kws", isDaemon = true) {
            try {
                listen()
            } catch (e: Throwable) {
                Log.e(TAG, "voice loop failed", e)
            } finally {
                running = false
                capturing = false
                releaseMic()
                kwsStream?.release()
                kwsStream = null
                asrStream?.release()
                asrStream = null
                kws?.release()
                kws = null
                asr?.release()
                asr = null
            }
        }
    }

    private fun listen() {
        val spotter = KeywordSpotter(
            assetManager = assets,
            config = KeywordSpotterConfig(
                featConfig = getFeatureConfig(SAMPLE_RATE, 80),
                modelConfig = OnlineModelConfig(
                    transducer = OnlineTransducerModelConfig(
                        encoder = WakeKeywords.ENCODER,
                        decoder = WakeKeywords.DECODER,
                        joiner = WakeKeywords.JOINER,
                    ),
                    tokens = WakeKeywords.TOKENS,
                    numThreads = 1,
                    debug = false,
                    provider = "cpu",
                    modelType = "zipformer2",
                ),
                keywordsFile = WakeKeywords.KEYWORDS_FILE,
                keywordsScore = 1.8f,
                keywordsThreshold = 0.25f,
                numTrailingBlanks = 2,
            ),
        )
        kws = spotter
        val wakeStream = spotter.createStream(WakeKeywords.STREAM)
        kwsStream = wakeStream
        asr = runCatching { buildAsr() }.onFailure { Log.e(TAG, "asr init failed", it) }.getOrNull()

        val minBytes = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBytes <= 0) {
            Log.e(TAG, "AudioRecord buffer size invalid: $minBytes")
            stopSelf()
            return
        }
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            minBytes * 2,
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            Log.e(TAG, "AudioRecord failed to initialize")
            stopSelf()
            return
        }
        audioRecord = recorder
        recorder.startRecording()

        val chunk = (SAMPLE_RATE * 0.05).toInt()
        val buffer = ShortArray(chunk)
        var lastDetectAt = 0L
        var listenAt = 0L
        var skipUntil = 0L
        var lastEmitted = ""
        var gate = UtteranceGate()
        var vadMs = 0L

        while (running) {
            val n = recorder.read(buffer, 0, buffer.size)
            if (n <= 0) continue
            val samples = FloatArray(n) { buffer[it] / 32768.0f }
            val now = SystemClock.elapsedRealtime()
            val dtMs = n * 1000L / SAMPLE_RATE

            if (capturing) {
                val recognizer = asr
                if (recognizer == null) {
                    capturing = false
                    emitTranscript("", settled = true)
                    continue
                }
                var stream = asrStream
                if (stream == null) {
                    stream = recognizer.createStream()
                    asrStream = stream
                    // 喊小岸后要跳过提示音；对话窗口里直接听，不跳，别把开头几个字丢了
                    skipUntil = now + (if (chimePending) CHIME_SKIP_MS else 0L)
                    chimePending = false
                    listenAt = skipUntil
                    lastEmitted = ""
                    asrAt = 0L
                    loggedPartial = false
                    gate = UtteranceGate()
                }
                if (now < skipUntil) continue
                if (asrAt == 0L) {
                    asrAt = now
                    HelmLatency.log(currentTurn, "asr_start", "since_wake_ms=${now - turnAt}")
                }
                stream.acceptWaveform(samples, SAMPLE_RATE)
                while (recognizer.isReady(stream)) {
                    recognizer.decode(stream)
                }
                val text = AsrModels.display(recognizer.getResult(stream).text)
                if (text.isNotBlank() && text != lastEmitted) {
                    lastEmitted = text
                    gate.noteHeard()
                    if (!loggedPartial) {
                        loggedPartial = true
                        HelmLatency.log(
                            currentTurn,
                            "asr_partial",
                            "ms=${now - asrAt} chars=${text.length}",
                        )
                    }
                    emitTranscript(text, settled = false)
                }
                val dtMs = n * 1000L / SAMPLE_RATE
                val elapsed = (now - listenAt).coerceAtLeast(0L)
                gate.onAudio(audioRms(samples), dtMs)
                val reason = gate.shouldStop(elapsed)
                if (reason != null) {
                    Log.i(TAG, "asr stop ${reason.name} elapsed=${elapsed}ms heard=${gate.heard}")
                    capturing = false
                    asrStream = null
                    val finalText = finishUtterance(recognizer, stream, lastEmitted)
                    HelmLatency.log(
                        currentTurn,
                        "asr_end",
                        "ms=$elapsed reason=${reason.name} chars=${finalText.length} heard=${if (gate.heard) 1 else 0}",
                    )
                    lastDetectAt = SystemClock.elapsedRealtime()
                    spotter.reset(wakeStream)
                }
            } else {
                // 对话窗口内：不用喊小岸，持续人声够 300ms 就直接进听写
                if (ConversationBus.bypass(now)) {
                    if (audioRms(samples) >= VAD_RMS) vadMs += dtMs else vadMs = 0L
                    if (vadMs >= VAD_TRIGGER_MS) {
                        vadMs = 0L
                        onHeardSpeech()
                    }
                } else {
                    vadMs = 0L
                }
                if (!capturing) {
                    wakeStream.acceptWaveform(samples, SAMPLE_RATE)
                    while (spotter.isReady(wakeStream)) {
                        spotter.decode(wakeStream)
                        val keyword = spotter.getResult(wakeStream).keyword
                        if (keyword.isBlank()) continue
                        if (now - lastDetectAt < COOLDOWN_MS) {
                            spotter.reset(wakeStream)
                            continue
                        }
                        lastDetectAt = now
                        spotter.reset(wakeStream)
                        onDetected(keyword)
                    }
                }
            }
        }
    }

    private fun finishUtterance(
        recognizer: OnlineRecognizer,
        stream: OnlineStream,
        lastEmitted: String,
    ): String {
        stream.inputFinished()
        while (recognizer.isReady(stream)) {
            recognizer.decode(stream)
        }
        val text = AsrModels.display(recognizer.getResult(stream).text).ifBlank { lastEmitted }
        emitTranscript(text, settled = true)
        stream.release()
        return text
    }

    private fun buildAsr(): OnlineRecognizer {
        return OnlineRecognizer(
            assetManager = assets,
            config = OnlineRecognizerConfig(
                featConfig = getFeatureConfig(SAMPLE_RATE, 80),
                modelConfig = OnlineModelConfig(
                    transducer = OnlineTransducerModelConfig(
                        encoder = AsrModels.ENCODER,
                        decoder = AsrModels.DECODER,
                        joiner = AsrModels.JOINER,
                    ),
                    tokens = AsrModels.TOKENS,
                    numThreads = 2,
                    debug = false,
                    provider = "cpu",
                    modelType = "zipformer2",
                ),
                enableEndpoint = false,
                decodingMethod = "greedy_search",
            ),
        )
    }

    private fun onDetected(keyword: String) {
        currentTurn = HelmLatency.newTurn()
        turnAt = SystemClock.elapsedRealtime()
        loggedPartial = false
        asrAt = 0L
        chimePending = true
        HelmLatency.log(currentTurn, "wake", "keyword=$keyword")
        Log.i(TAG, "wake word: $keyword")
        WakeChime.play()
        startActivity(MainActivity.wakeWordIntent(this, keyword, currentTurn))
        capturing = asr != null
        if (!capturing) {
            Log.w(TAG, "asr unavailable, wake only")
            HelmLatency.log(currentTurn, "asr_skip", "reason=unavailable")
            emitTranscript("", settled = true)
        }
    }

    /** 对话窗口内听到了人声：不开提示音、不亮屏，直接进听写。 */
    private fun onHeardSpeech() {
        currentTurn = HelmLatency.newTurn()
        turnAt = SystemClock.elapsedRealtime()
        loggedPartial = false
        asrAt = 0L
        chimePending = false
        HelmLatency.log(currentTurn, "conversation_listen")
        capturing = asr != null
        if (!capturing) {
            HelmLatency.log(currentTurn, "asr_skip", "reason=unavailable")
            emitTranscript("", settled = true)
        }
    }

    private fun emitTranscript(text: String, settled: Boolean) {
        sendBroadcast(
            Intent(ACTION_TRANSCRIPT).setPackage(packageName).apply {
                putExtra(EXTRA_TEXT, text)
                putExtra(EXTRA_SETTLED, settled)
                putExtra(EXTRA_TURN, currentTurn)
            },
        )
    }

    private fun releaseMic() {
        val recorder = audioRecord
        audioRecord = null
        if (recorder == null) return
        runCatching { recorder.stop() }
        recorder.release()
    }

    private fun startInForeground() {
        val pending = PendingIntent.getActivity(
            this,
            1,
            Intent(this, MainActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_helm)
            .setContentTitle(getString(R.string.wake_word_title))
            .setContentText(getString(R.string.wake_word_text))
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
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
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
                getString(R.string.wake_word_channel),
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                setShowBadge(false)
                description = getString(R.string.wake_word_text)
            },
        )
    }

    companion object {
        private const val TAG = "WakeWord"
        private const val CHANNEL_ID = "wake_word"
        private const val NOTIFICATION_ID = 48
        private const val SAMPLE_RATE = 16_000
        private const val COOLDOWN_MS = 1_000L
        private const val CHIME_SKIP_MS = 500L
        private const val VAD_TRIGGER_MS = 300L
        private const val VAD_RMS = 0.030f

        const val ACTION_TRANSCRIPT = "cn.weiekko.dock.VOICE_TRANSCRIPT"
        const val EXTRA_TEXT = "text"
        const val EXTRA_SETTLED = "settled"
        const val EXTRA_TURN = "turn_id"

        fun start(context: Context) {
            val intent = Intent(context, WakeWordService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, WakeWordService::class.java))
        }
    }
}
