package cn.weiekko.dock.media

import cn.weiekko.dock.data.MediaInfo

/**
 * 主屏每秒读一次手机媒体状态，但每次 `updatedAt` 都是新的，
 * MediaInfo 因此永远不相等 -> StateFlow 每秒当成新值发一遍 -> 整屏重组。
 *
 * 这里把「内容没变」的快照原样换回上一份：相等就不发，真变了才带新时间戳。
 */
class MediaSnapshotKeeper {
    private var last: MediaInfo? = null

    fun keep(fresh: MediaInfo): MediaInfo {
        val prev = last
        if (prev != null && sameContent(prev, fresh)) return prev
        last = fresh
        return fresh
    }

    private fun sameContent(a: MediaInfo, b: MediaInfo): Boolean =
        a.online == b.online &&
            a.playing == b.playing &&
            a.title == b.title &&
            a.artist == b.artist &&
            a.app == b.app
}
