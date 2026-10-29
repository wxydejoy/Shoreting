package cn.weiekko.dock.background

import java.net.Inet4Address
import java.net.NetworkInterface

fun preferLanIpv4(addresses: List<String>): String? {
    return addresses.firstOrNull { it.startsWith("10.") }
        ?: addresses.firstOrNull { it.startsWith("192.168.") }
        ?: addresses.firstOrNull { isPrivate172(it) }
}

fun lanIpv4(): String? {
    val found = mutableListOf<String>()
    val interfaces = NetworkInterface.getNetworkInterfaces() ?: return null
    for (network in interfaces) {
        if (!network.isUp || network.isLoopback) continue
        for (address in network.inetAddresses) {
            if (address is Inet4Address && !address.isLoopbackAddress) {
                address.hostAddress?.let(found::add)
            }
        }
    }
    return preferLanIpv4(found)
}

private fun isPrivate172(address: String): Boolean {
    val parts = address.split('.')
    if (parts.size != 4 || parts[0] != "172") return false
    val second = parts[1].toIntOrNull() ?: return false
    return second in 16..31
}
