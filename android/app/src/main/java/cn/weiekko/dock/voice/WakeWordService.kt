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
    private var asrLoader: Thread? = null
    @Volatile
    private var asr: OnlineRecognizer? = null
    /** 模型装不起来（文件缺失之类），之后唤醒就只亮灯、不识别。 */
    @Volatile
    private var asrFailed = false
    private var asrStream: OnlineStream? = null
    private var audioRecord: AudioRecord? = null
    /** 服务在拆了，迟到的模型要自己收掉。 */
    @Volatile
    private var closed = false
    @Volatile
    private var currentTurn = ""
    @Volatile
    private var chimePending = false
    /** 采集线程重开过录音器，解码线程据此丢掉手上的流。 */
    @Volatile
    private var micGap = false
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
        closed = true
        capturing = false
        recordThread?.join(400)
        asrLoader?.join(ASR_LOAD_JOIN_MS)
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
        closed = false
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
        // ASR 模型比唤醒词大一个量级，加载要几百毫秒。原来是在这里同步加载，
        // 麦克风要等它加载完才开：开机头一秒喊「小岸」是没人应的。
        // 现在先开麦，模型在后台线程里装，装好之前叫醒的那一句会等它一会儿。
        startAsrLoader()

        var recorder: AudioRecord? = openRecorder() ?: run {
            stopSelf()
            return
        }
        val faults = MicFaults()
        faults.opened()

        val queue = AudioChunkQueue(QUEUE_CHUNKS)
        val worker = thread(name = "wake-decode", isDaemon = true) {
            try {
                decodeLoop(spotter, wakeStream, queue)
            } catch (e: Throwable) {
                Log.e(TAG, "decode loop failed", e)
                running = false
            }
        }

        val chunk = (SAMPLE_RATE * 0.05).toInt()
        val buffer = ShortArray(chunk)
        try {
            while (running) {
                val mic = recorder ?: break
                val n = mic.read(buffer, 0, buffer.size)
                if (n > 0) {
                    faults.data()
                    queue.offer(FloatArray(n) { buffer[it] / 32768.0f })
                    continue
                }
                if (n == 0) {
                    // 这一拍没数据，不是错误，歇一下免得空转
                    pause(READ_EMPTY_MS)
                    continue
                }
                when (val action = faults.onError(SystemClock.elapsedRealtime())) {
                    is MicAction.Wait -> {
                        Log.w(TAG, "mic read error code=$n, retry in ${action.ms}ms")
                        pause(action.ms)
                    }
                    MicAction.Reopen -> {
                        // 麦克风被抢走或者驱动重挂：收掉重开。解码线程那边收到
                        // micGap 后会把手上的流丢掉，不留半句旧音频。
                        Log.w(TAG, "mic read error code=$n, reopening recorder")
                        runCatching { mic.stop() }
                        mic.release()
                        if (audioRecord === mic) audioRecord = null
                        val next = openRecorder()
                        if (next == null) {
                            Log.e(TAG, "recorder reopen failed, stopping voice")
                            recorder = null
                            micGap = true
                            running = false
                            break
                        }
                        recorder = next
                        faults.opened()
                        micGap = true
                    }
                }
            }
        } finally {
            closed = true
            queue.close()
            worker.join(400)
            asrLoader?.join(ASR_LOAD_JOIN_MS)
            recorder?.let { mic ->
                if (audioRecord === mic) audioRecord = null
                runCatching { mic.release() }
            }
        }
    }

    /** 按当前参数开一个录音器；开不起来返回 null（调用方决定停不停）。 */
    private fun openRecorder(): AudioRecord? {
        val minBytes = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBytes <= 0) {
            Log.e(TAG, "AudioRecord buffer size invalid: $minBytes")
            return null
        }
        // 采集和解码分两条线程：采集只做搬运，内置缓冲给到 400ms，
        // 解码偶尔慢一拍也不会溢出去丢掉「正在说的那几个字」。
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minBytes * 4, SAMPLE_RATE * 2 * MIC_BUFFER_MS / 1000),
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            Log.e(TAG, "AudioRecord failed to initialize")
            return null
        }
        audioRecord = recorder
        recorder.startRecording()
        return recorder
    }

    /** 分段睡，服务被收掉时能马上停下来。 */
    private fun pause(ms: Long) {
        var left = ms
        while (left > 0 && running) {
            val step = left.coerceAtMost(50L)
            Thread.sleep(step)
            left -= step
        }
    }

    /**
     * 唤醒词和流式 ASR 都在这里跑，输入是采集线程送来的块。
     *
     * 处理进度按「已经喂进去的音频时长」算，而不是墙钟：解码偶尔积压时，
     * 墙钟会虚报一句已经说了多久，把尾静音判定提前触发、半句话被截断。
     */
    private fun decodeLoop(
        spotter: KeywordSpotter,
        wakeStream: OnlineStream,
        queue: AudioChunkQueue,
    ) {
        var lastDetectAt = 0L
        var processedMs = 0L
        var skipMs = 0L
        var lastEmitted = ""
        var gate = UtteranceGate()
        var vadMs = 0L
        var bargeMs = 0L

        while (running) {
            if (micGap) {
                // 录音器重开过，音频断了一截：旧的一句不能再往下拼，
                // 唤醒词也把缓冲清掉，免得拿断档前后的音频凑出一句话。
                micGap = false
                queue.clear()
                if (capturing) {
                    capturing = false
                    asrStream?.release()
                    asrStream = null
                    emitTranscript("", settled = true)
                }
                spotter.reset(wakeStream)
            }
            if (capturing && asr == null && !asrFailed &&
                SystemClock.elapsedRealtime() - turnAt < ASR_WAIT_MS
            ) {
                // 模型还在后台装：先把音频留在队列里排队，别把这一句吃掉。
                Thread.sleep(ASR_POLL_MS)
                continue
            }
            val samples = queue.poll(QUEUE_POLL_MS) ?: continue
            val n = samples.size
            val now = SystemClock.elapsedRealtime()
            val dtMs = n * 1000L / SAMPLE_RATE

            if (capturing) {
                val recognizer = asr
                if (recognizer == null) {
                    capturing = false
                    emitTranscript("", settled = true)
                    HelmLatency.log(currentTurn, "asr_miss", "reason=model_not_ready")
                    continue
                }
                var stream = asrStream
                if (stream == null) {
                    stream = recognizer.createStream()
                    asrStream = stream
                    // 喊小岸后要跳过提示音；对话窗口里直接听，不跳，别把开头几个字丢了。
                    // 只丢「音频」不按墙钟丢：等过模型之后队列里排着的是刚唤醒时的
                    // 音频，按墙钟会把用户的话丢掉。
                    skipMs = if (chimePending) CHIME_SKIP_MS else 0L
                    chimePending = false
                    processedMs = 0L
                    lastEmitted = ""
                    asrAt = 0L
                    loggedPartial = false
                    gate = UtteranceGate()
                }
                if (skipMs > 0L) {
                    skipMs -= dtMs
                    continue
                }
                processedMs += dtMs
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
                gate.onAudio(audioRms(samples), dtMs)
                val reason = gate.shouldStop(processedMs)
                if (reason != null) {
                    Log.i(
                        TAG,
                        "asr stop ${reason.name} elapsed=${processedMs}ms heard=${gate.heard} " +
                            "dropped=${queue.dropped}",
                    )
                    capturing = false
                    asrStream = null
                    val finalText = finishUtterance(recognizer, stream, lastEmitted)
                    HelmLatency.log(
                        currentTurn,
                        "asr_end",
                        "ms=$processedMs reason=${reason.name} chars=${finalText.length} " +
                            "heard=${if (gate.heard) 1 else 0}",
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
                // 插话打断（F2）：她正在说时，明显的人声（更响、持续更久）直接抢过来。
                // 阈值比窗口内的高：免得她自己的声音从 Mini 喇叭飘过来就把自己打断
                if (!capturing && ConversationBus.speaking(now)) {
                    if (audioRms(samples) >= BARGE_RMS) bargeMs += dtMs else bargeMs = 0L
                    if (bargeMs >= BARGE_TRIGGER_MS) {
                        bargeMs = 0L
                        onHeardSpeech(barge = true)
                    }
                } else {
                    bargeMs = 0L
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

    /**
     * 模型在后台线程里装：麦克风和唤醒词先上线，装好了再把它挂上。
     * 服务正在拆的话，装出来的模型自己 release 掉，别漏一份几百兆的 native 内存。
     */
    private fun startAsrLoader() {
        asrLoader = thread(name = "asr-load", isDaemon = true) {
            val started = SystemClock.elapsedRealtime()
            val built = runCatching { buildAsr() }
                .onFailure { Log.e(TAG, "asr init failed", it) }
                .getOrNull()
            val cost = SystemClock.elapsedRealtime() - started
            if (built == null) {
                asrFailed = true
                HelmLatency.log(currentTurn.ifBlank { "-" }, "asr_model", "ms=$cost failed=1")
                return@thread
            }
            if (closed) {
                built.release()
                return@thread
            }
            asr = built
            Log.i(TAG, "asr ready in ${cost}ms")
            HelmLatency.log(currentTurn.ifBlank { "-" }, "asr_model", "ms=$cost")
        }
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
        capturing = asr != null || !asrFailed
        if (asr == null) {
            if (asrFailed) {
                Log.w(TAG, "asr unavailable, wake only")
                HelmLatency.log(currentTurn, "asr_skip", "reason=unavailable")
                emitTranscript("", settled = true)
            } else {
                // 模型还在装：解码线程会等它（最多 ASR_WAIT_MS）再开始识别
                HelmLatency.log(currentTurn, "asr_wait", "model=loading")
            }
        }
    }

    /** 对话窗口内听到了人声：不开提示音、不亮屏，直接进听写。 */
    private fun onHeardSpeech(barge: Boolean = false) {
        currentTurn = HelmLatency.newTurn()
        turnAt = SystemClock.elapsedRealtime()
        loggedPartial = false
        asrAt = 0L
        chimePending = false
        HelmLatency.log(currentTurn, if (barge) "barge_listen" else "conversation_listen")
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
        private const val BARGE_TRIGGER_MS = 800L
        private const val BARGE_RMS = 0.045f

        /** AudioRecord 内置缓冲：400ms 的 16bit 单声道。 */
        private const val MIC_BUFFER_MS = 400

        /** 队列最多存 2s 音频；再慢就丢最旧的，别把采集线程堵住。 */
        private const val QUEUE_CHUNKS = 40
        private const val QUEUE_POLL_MS = 120L

        /** read 返回 0（这一拍没数据）时歇一下，别把核跑满。 */
        private const val READ_EMPTY_MS = 20L

        /** 唤醒时模型还没装好的话，最多等这么久再放弃这一句。 */
        private const val ASR_WAIT_MS = 1_200L
        private const val ASR_POLL_MS = 20L
        private const val ASR_LOAD_JOIN_MS = 1_500L

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
