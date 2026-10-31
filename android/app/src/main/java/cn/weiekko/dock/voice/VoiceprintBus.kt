package cn.weiekko.dock.voice

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * ViewModel 与 WakeWordService 之间的声纹桥（对齐 ConversationBus 的角色）：
 * VM 写开关和注册目标；服务每句话都读，写回注册进度，设置页显示「收到第几句」。
 */
object VoiceprintBus {

    /** 注册一位新声音要凑几句。 */
    const val ENROLL_UTTERANCES = 3

    /** 声纹总开关：默认关；服务热读，不用重启。 */
    @Volatile
    var enabled: Boolean = false

    /** 正在给谁注册；null = 不在注册。注册完成服务自己清掉。 */
    @Volatile
    var enrollName: String? = null

    data class EnrollState(
        val name: String,
        val collected: Int,
        val need: Int,
        val saved: Boolean = false,
        val error: String? = null,
    )

    private val _enroll = MutableStateFlow<EnrollState?>(null)
    val enroll: StateFlow<EnrollState?> = _enroll.asStateFlow()

    fun report(state: EnrollState?) {
        _enroll.value = state
    }
}
