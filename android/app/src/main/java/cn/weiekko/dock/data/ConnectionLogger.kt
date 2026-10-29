package cn.weiekko.dock.data
import java.io.File
import java.util.Date
fun logConnection(msg: String) {
    try {
        val f = File("/data/data/cn.weiekko.dock/files/connection.log")
        f.parentFile?.mkdirs()
        f.appendText("[${Date()}] $msg\n")
    } catch (e: Exception) { e.printStackTrace() }
}
