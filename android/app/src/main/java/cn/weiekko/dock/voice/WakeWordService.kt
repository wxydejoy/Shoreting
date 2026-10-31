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
import java.io.File
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
import java.util.concurrent.Callable
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.TimeUnit
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
    @Volatile
    private var asrLoading = false
    /** 现在这只识别器用的是第几版热词表；-1 表示它没带热词。 */
    @Volatile
    private var builtHotwords = -1
    /** 带热词装不起来过，就别每句话都再试一遍。 */
    @Volatile
    private var hotwordsDisabled = false

    private val hotwordFile: File by lazy { File(cacheDir, "asr-hotwords.txt") }

    /**
     * 解包到 filesDir 的模型目录。识别器走文件模式（见 [AsrAssets]），
     * 否则热词的绝对路径会被 sherpa 当 asset 名去开，native abort 掉整个进程。
     */
    @Volatile
    private var asrDir: File? = null

    /** tokens.txt 里的字，用来把词表里模型不认识的字剔掉（否则热词整表失效）。 */
    private val asrTokens: Set<String>? by lazy {
        runCatching {
            assets.open(AsrModels.TOKENS).bufferedReader().use {
                AsrHotwords.parseTokens(it.readText())
            }
        }.onFailure { Log.w(TAG, "tokens unreadable, hotwords unfiltered", it) }.getOrNull()
    }
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
    /** F5 声纹：模型懒加载，第一次用到才装；装不起来就当这功能不存在。 */
    private var voiceprint: VoiceprintEngine? = null
    /** 正在识别的那句话的音频（只在本机内存里，说完就清）。 */
    private val utteranceAudio = UtteranceAudio()
    private var enrollBuffer: EnrollmentBuffer? = null
    private var enrollTarget: String? = null
    /** 声纹的活儿（模型加载、算向量、读花名册）都排这一条线程，别压解码线程。 */
    private val vpExecutor: ExecutorService = Executors.newSingleThreadExecutor { r ->
        Thread(r, "voiceprint").apply { isDaemon = true }
    }
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
        voiceprint?.release()
        voiceprint = null
        vpExecutor.shutdownNow()
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
        var partial = StablePartial()

        while (running) {
            if (micGap) {
                // 录音器重开过，音频断了一截：旧的一句不能再往下拼，
                // 唤醒词也把缓冲清掉，免得拿断档前后的音频凑出一句话。
                micGap = false
                queue.clear()
                utteranceAudio.clear()
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
            if (!capturing && asr != null && !asrLoading && !hotwordsDisabled &&
                builtHotwords != AsrHotwords.version
            ) {
                // 设备名变了（新 snapshot 下来）：热词表要重装，下一句生效。
                // 挑没人说话的时候做，装的时候新唤醒会等它一会儿。
                Log.i(TAG, "hotwords v${AsrHotwords.version}, rebuilding asr")
                asr = null
                startAsrLoader()
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
                    utteranceAudio.clear()
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
                    partial = StablePartial()
                }
                if (skipMs > 0L) {
                    skipMs -= dtMs
                    continue
                }
                processedMs += dtMs
                // F5：声纹开着（或在注册）才攒这轮音频，平时一分内存都不占
                if (VoiceprintBus.enabled || VoiceprintBus.enrollName != null) {
                    utteranceAudio.add(samples)
                }
                if (asrAt == 0L) {
                    asrAt = now
                    HelmLatency.log(currentTurn, "asr_start", "since_wake_ms=${now - turnAt}")
                }
                stream.acceptWaveform(samples, SAMPLE_RATE)
                while (recognizer.isReady(stream)) {
                    recognizer.decode(stream)
                }
                val text = AsrModels.display(recognizer.getResult(stream).text)
                if (text.isNotBlank()) {
                    gate.noteHeard()
                    lastEmitted = text
                    // 只往后长 + 节流：字幕别抖，也别一句话重组十几次主屏
                    val fresh = partial.offer(text, now)
                    if (fresh != null) {
                        if (!loggedPartial) {
                            loggedPartial = true
                            HelmLatency.log(
                                currentTurn,
                                "asr_partial",
                                "ms=${now - asrAt} chars=${fresh.length}",
                            )
                        }
                        emitTranscript(fresh, settled = false)
                    }
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
                    // F5：认人跟收尾解码并行——她先开口，声纹在旁边算，发广播前等一小会儿
                    val speakerJob = startSpeakerTask(gate.heard)
                    val finalText = finishUtterance(recognizer, stream, lastEmitted)
                    val speaker = awaitSpeaker(speakerJob)
                    emitTranscript(finalText, settled = true, speaker = speaker)
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
        stream.release()
        return text
    }

    /**
     * F5：把这轮的认人任务排进声纹线程，跟收尾解码并行跑，别让她等声纹算完才开口。
     * 返回 null 表示这轮不认人（没开声纹、没在注册、或这轮没听到什么）。
     * 音频不出设备——攒的那份随取随清，出门的只有「认出的称呼」。
     */
    private fun startSpeakerTask(heardSomething: Boolean): Future<String>? {
        val target = VoiceprintBus.enrollName
        if (!VoiceprintBus.enabled && target == null) {
            utteranceAudio.clear()
            return null
        }
        val samples = utteranceAudio.samples()
        utteranceAudio.clear()
        if (!heardSomething || samples.size < EMBED_MIN_SAMPLES) return null
        return vpExecutor.submit(Callable { settleSpeaker(samples, target) })
    }

    /** 声纹线程上跑：一句音频 → 向量 → 注册或认人。出任何岔子都当「没认出来」。 */
    private fun settleSpeaker(samples: FloatArray, target: String?): String {
        val engine = voiceprintEngine() ?: return ""
        val startedAt = SystemClock.elapsedRealtime()
        val embedding = engine.embed(samples) ?: return ""
        val cost = SystemClock.elapsedRealtime() - startedAt
        val store = VoiceprintStore(File(filesDir, VOICEPRINT_FILE))
        if (target != null) {
            enroll(embedding, target, store)
            return ""
        }
        val roster = store.load()
        val name = roster.match(embedding)
        HelmLatency.log(
            currentTurn,
            "voiceprint",
            "ms=$cost matched=${name ?: "-"} speakers=${roster.names().size}",
        )
        return name ?: ""
    }

    /** 最多陪声纹线程等这么久；等不到这轮就不带称呼——对话本身不等声纹。 */
    private fun awaitSpeaker(job: Future<String>?): String {
        if (job == null) return ""
        return try {
            job.get(SPEAKER_JOIN_MS, TimeUnit.MILLISECONDS)
        } catch (e: Exception) {
            job.cancel(true)
            ""
        }
    }

    /** 注册：同一目标凑满 N 句取均值落盘；换了目标（或取消过）就重攒。 */
    private fun enroll(embedding: FloatArray, target: String, store: VoiceprintStore) {
        if (enrollTarget != target) {
            enrollTarget = target
            enrollBuffer = EnrollmentBuffer()
        }
        val buffer = enrollBuffer ?: EnrollmentBuffer().also { enrollBuffer = it }
        val collected = buffer.offer(embedding)
        if (!buffer.complete()) {
            VoiceprintBus.report(
                VoiceprintBus.EnrollState(target, collected, VoiceprintBus.ENROLL_UTTERANCES),
            )
            HelmLatency.log(currentTurn, "voiceprint_enroll", "name=$target got=$collected")
            return
        }
        runCatching { store.save(store.load().enrolled(target, buffer.average())) }
            .onSuccess {
                VoiceprintBus.report(
                    VoiceprintBus.EnrollState(
                        target,
                        collected,
                        VoiceprintBus.ENROLL_UTTERANCES,
                        saved = true,
                    ),
                )
                HelmLatency.log(currentTurn, "voiceprint_enroll", "name=$target ok=1")
            }
            .onFailure {
                VoiceprintBus.report(
                    VoiceprintBus.EnrollState(
                        target,
                        collected,
                        VoiceprintBus.ENROLL_UTTERANCES,
                        error = "没存下来，再试一次",
                    ),
                )
                HelmLatency.log(currentTurn, "voiceprint_enroll", "name=$target ok=0")
            }
        VoiceprintBus.enrollName = null
        enrollTarget = null
        enrollBuffer = null
    }

    private fun voiceprintEngine(): VoiceprintEngine? {
        val current = voiceprint
        if (current != null) return if (current.failed) null else current
        val built = VoiceprintEngine(assets)
        voiceprint = built
        return if (built.failed) null else built
    }

    /**
     * 模型在后台线程里装：麦克风和唤醒词先上线，装好了再把它挂上。
     * 服务正在拆的话，装出来的模型自己 release 掉，别漏一份几百兆的 native 内存。
     *
     * 热词表（设备名 + 常用指令）在 init 时就被读进 native，改不了，所以换词表
     * 只能整只重装。带热词装不起来就退回不带热词的 greedy，别为了准确率把
     * 语音整个搞哑。
     */
    private fun startAsrLoader() {
        if (asrLoading) return
        asrLoading = true
        asrLoader = thread(name = "asr-load", isDaemon = true) {
            val version = AsrHotwords.version
            val started = SystemClock.elapsedRealtime()
            // 首次要把 155MB 模型从 assets 解到 filesDir；唤醒词不受影响，它还在 assets 上
            val dir = asrDir ?: AsrAssets.ensure(this@WakeWordService).also { asrDir = it }
            if (dir == null) Log.e(TAG, "asr models unavailable, wake-only")
            val hotwords = prepareHotwords()
            var usedHotwords = false
            var built = hotwords?.let { path ->
                runCatching { buildAsr(path) }
                    .onFailure { Log.w(TAG, "asr with hotwords failed, retrying plain", it) }
                    .getOrNull()
                    ?.also { usedHotwords = true }
            }
            if (built == null) {
                if (hotwords != null) hotwordsDisabled = true
                built = runCatching { buildAsr(null) }
                    .onFailure { Log.e(TAG, "asr init failed", it) }
                    .getOrNull()
            }
            val cost = SystemClock.elapsedRealtime() - started
            asrLoading = false
            if (built == null) {
                asrFailed = true
                HelmLatency.log(currentTurn.ifBlank { "-" }, "asr_model", "ms=$cost failed=1")
                return@thread
            }
            if (closed) {
                built.release()
                return@thread
            }
            val old = asr
            asr = built
            builtHotwords = if (usedHotwords) version else -1
            old?.release()
            Log.i(TAG, "asr ready in ${cost}ms hotwords=${if (usedHotwords) 1 else 0}")
            HelmLatency.log(
                currentTurn.ifBlank { "-" },
                "asr_model",
                "ms=$cost hotwords=${if (usedHotwords) 1 else 0}",
            )
        }
    }

    /** 把当前热词表写到缓存目录（native 只认路径），没有可用词就返回 null。 */
    private fun prepareHotwords(): String? {
        val text = AsrHotwords.forStream(asrTokens)
        if (text.isBlank()) return null
        return runCatching {
            hotwordFile.writeText(text + "\n")
            hotwordFile.absolutePath
        }.onFailure { Log.w(TAG, "hotwords file failed", it) }.getOrNull()
    }

    private fun buildAsr(hotwordsPath: String?): OnlineRecognizer {
        val dir = asrDir ?: throw IllegalStateException("asr models not extracted")
        fun abs(name: String) = AsrAssets.path(dir, name)
        return OnlineRecognizer(
            // 必须是 null：非 null 时 sherpa 会把下面 hotwordsPath 的绝对路径当 asset 名开
            assetManager = null,
            config = OnlineRecognizerConfig(
                featConfig = getFeatureConfig(SAMPLE_RATE, 80),
                modelConfig = OnlineModelConfig(
                    transducer = OnlineTransducerModelConfig(
                        encoder = abs(AsrModels.ENCODER),
                        decoder = abs(AsrModels.DECODER),
                        joiner = abs(AsrModels.JOINER),
                    ),
                    tokens = abs(AsrModels.TOKENS),
                    numThreads = 2,
                    debug = false,
                    provider = "cpu",
                    modelType = "zipformer2",
                    // 热词要按 modeling_unit 编码；不给会被当成空串，EncodeHotwords 直接 exit(255)。
                    // cjkchar 与 AsrHotwords.toTokens 的按字空格写法一致。
                    modelingUnit = "cjkchar",
                ),
                enableEndpoint = false,
                // 热词只在 modified_beam_search 下生效；没词表就走 greedy，省点算力
                decodingMethod = if (hotwordsPath == null) "greedy_search" else "modified_beam_search",
                maxActivePaths = if (hotwordsPath == null) 4 else HOTWORDS_PATHS,
                hotwordsFile = hotwordsPath.orEmpty(),
                hotwordsScore = HOTWORDS_SCORE,
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

    private fun emitTranscript(text: String, settled: Boolean, speaker: String = "") {
        sendBroadcast(
            Intent(ACTION_TRANSCRIPT).setPackage(packageName).apply {
                putExtra(EXTRA_TEXT, text)
                putExtra(EXTRA_SETTLED, settled)
                putExtra(EXTRA_TURN, currentTurn)
                putExtra(EXTRA_SPEAKER, speaker)
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

        /** 热词权重与 beam 宽度：权重偏高一点，设备名才压得住同音字。 */
        private const val HOTWORDS_SCORE = 1.6f
        private const val HOTWORDS_PATHS = 4

        /** 唤醒时模型还没装好的话，最多等这么久再放弃这一句。 */
        private const val ASR_WAIT_MS = 1_200L
        private const val ASR_POLL_MS = 20L
        private const val ASR_LOAD_JOIN_MS = 1_500L

        /** 发 settled 广播前最多陪声纹线程等这么久；等不到这轮就不认人。 */
        private const val SPEAKER_JOIN_MS = 400L

        const val ACTION_TRANSCRIPT = "cn.weiekko.dock.VOICE_TRANSCRIPT"
        const val EXTRA_TEXT = "text"
        const val EXTRA_SETTLED = "settled"
        const val EXTRA_TURN = "turn_id"
        const val EXTRA_SPEAKER = "speaker"

        /** 花名册文件名（应用私有目录，不出设备）。 */
        private const val VOICEPRINT_FILE = "voiceprint.json"

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
