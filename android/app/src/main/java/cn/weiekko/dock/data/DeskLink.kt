package cn.weiekko.dock.data

enum class DeskLink {
    Live,
    Local,
    Preview,
    ;

    val pollsHub: Boolean get() = this == Live
    val usesDemo: Boolean get() = this == Preview
    val companionBlock: String? get() = if (this == Local) HUB_OFF_COMPANION else null
}

const val HUB_OFF_COMPANION = "先在设置里打开 Hub"

fun deskLink(hubEnabled: Boolean, configured: Boolean): DeskLink = when {
    !hubEnabled -> DeskLink.Local
    configured -> DeskLink.Live
    else -> DeskLink.Preview
}
