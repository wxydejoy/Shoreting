package cn.weiekko.dock.power

enum class ScreenCommand {
    Sleep,
    Wake,
    VoiceWake,
    /** 协议增补提案：snapshot.lock 的熄屏请求——设备管理员 lockNow，真锁屏。 */
    LockNow,
}
