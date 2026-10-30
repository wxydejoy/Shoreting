package cn.weiekko.dock.power

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.annotation.OptIn
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.lifecycle.LifecycleOwner
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.face.FaceDetection
import com.google.mlkit.vision.face.FaceDetectorOptions
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * 在场检测的摄像头一侧（F24）：前置摄像头 + MLKit 人脸检测，约 5fps，
 * 只把「有人 / 没人」布尔值从 [onPerson] 送出去。
 *
 * 隐私底线：帧只在内存里过一遍检测器，**不落盘、不上传**，这个类里没有
 * 任何存储路径，也不该有。屏幕压暗（deskSleeping）时 activity 还活着，
 * 检测继续跑，人才有「回来」的那一刻；用户按电源键锁屏后相机停走，
 * 那之后就等手动唤醒，不强求。
 */
class CameraPresence(
    context: Context,
    private val lifecycleOwner: LifecycleOwner,
    private val onPerson: (Boolean) -> Unit,
) {
    private val appContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())
    private val executor: ExecutorService = Executors.newSingleThreadExecutor()
    private val detector = FaceDetection.getClient(
        FaceDetectorOptions.Builder()
            .setPerformanceMode(FaceDetectorOptions.PERFORMANCE_MODE_FAST)
            .setMinFaceSize(0.3f)
            .build(),
    )
    private var provider: ProcessCameraProvider? = null
    private var bound = false

    private val analysis = ImageAnalysis.Builder()
        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
        .build()
        .apply { setAnalyzer(executor, FrameReader()) }

    /** 节流到约 5fps：亮屏要跟手，但不用烧满摄像头帧率。 */
    private inner class FrameReader : ImageAnalysis.Analyzer {
        private var lastRunAt = 0L

        @OptIn(ExperimentalGetImage::class)
        override fun analyze(proxy: ImageProxy) {
            val now = SystemClock.elapsedRealtime()
            if (now - lastRunAt < FRAME_INTERVAL_MS) {
                proxy.close()
                return
            }
            lastRunAt = now
            val media = try {
                proxy.image
            } catch (error: Throwable) {
                if (error is OutOfMemoryError) throw error
                proxy.close()
                return
            }
            if (media == null) {
                proxy.close()
                return
            }
            val input = InputImage.fromMediaImage(media, proxy.imageInfo.rotationDegrees)
            val faces = try {
                detector.process(input)
            } catch (error: Throwable) {
                // 检测失败不当成「没人」：静默丢帧，等下一帧
                if (error is OutOfMemoryError) throw error
                proxy.close()
                return
            }
            // 帧在 complete 前必须保持打开，检测结果回来才能关
            faces.addOnCompleteListener { task ->
                runCatching { onPerson(task.isSuccessful && !task.result.isEmpty()) }
                proxy.close()
            }
        }
    }

    fun start() {
        mainHandler.post {
            if (bound) return@post
            val future = ProcessCameraProvider.getInstance(appContext)
            future.addListener(
                {
                    val cameraProvider = runCatching { future.get() }.getOrNull() ?: return@addListener
                    provider = cameraProvider
                    runCatching {
                        cameraProvider.unbindAll()
                        cameraProvider.bindToLifecycle(
                            lifecycleOwner,
                            CameraSelector.DEFAULT_FRONT_CAMERA,
                            analysis,
                        )
                    }
                    bound = true
                },
                mainHandler::post,
            )
        }
    }

    fun stop() {
        mainHandler.post {
            if (!bound) return@post
            bound = false
            runCatching { provider?.unbindAll() }
        }
    }

    fun release() {
        stop()
        executor.shutdown()
        runCatching { detector.close() }
    }

    companion object {
        const val FRAME_INTERVAL_MS = 200L
    }
}
