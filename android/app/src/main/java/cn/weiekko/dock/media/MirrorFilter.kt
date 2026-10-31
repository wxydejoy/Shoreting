package cn.weiekko.dock.media

/**
 * 通知镜像（F35）的过滤规矩。纯函数，测试不用起服务。
 *
 * 「哪些消息值得打断」很难调（蓝图原话），所以这里的默认姿态是**宁可不亮**：
 * 总开关默认关、白名单按包名一个一个填、常驻/汇总通知一律不要、自己发出的
 * 递话通知绝不回环。
 */
object MirrorFilter {
    /** 自己的包名：递话通知（F33）也是通知，回环进来就是无限复读。 */
    const val SELF_PACKAGE = "cn.weiekko.dock"

    /** 白名单原文 → 包名集合。逗号/中文逗号/换行/空格都能当分隔符。 */
    fun parseAppList(raw: String): List<String> = raw
        .split(',', '，', '\n', ' ', ';', '；')
        .map { it.trim() }
        .filter { it.isNotEmpty() }
        .distinct()

    fun shouldMirror(
        enabled: Boolean,
        apps: Set<String>,
        packageName: String,
        ongoing: Boolean,
        groupSummary: Boolean,
        text: CharSequence?,
    ): Boolean {
        if (!enabled) return false
        if (apps.isEmpty()) return false
        val pkg = packageName.trim()
        if (pkg.isEmpty() || pkg == SELF_PACKAGE) return false
        if (pkg !in apps) return false
        if (ongoing || groupSummary) return false
        // 没正文的通知（纯媒体进度、纯图标）亮出来也不知道是什么
        return !text?.toString().isNullOrBlank()
    }
}
