package cn.weiekko.dock.ui

import android.graphics.BitmapFactory
import android.net.Uri
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.InputStream

@Composable
fun ImageBackground(
    uri: String,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    var bitmap by remember(uri) { mutableStateOf<androidx.compose.ui.graphics.ImageBitmap?>(null) }
    LaunchedEffect(uri) {
        val decoded = withContext(Dispatchers.IO) {
            decodeBackground(context, Uri.parse(uri))
        }
        bitmap = decoded?.asImageBitmap()
    }
    val image = bitmap ?: return
    Image(
        bitmap = image,
        contentDescription = null,
        modifier = modifier.fillMaxSize(),
        contentScale = ContentScale.Crop,
    )
}

private fun decodeBackground(context: android.content.Context, uri: Uri): android.graphics.Bitmap? {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    openStream(context, uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
    val sample = BitmapFactory.Options().apply {
        inSampleSize = sampleSize(bounds.outWidth, bounds.outHeight, 2560)
    }
    return openStream(context, uri)?.use { BitmapFactory.decodeStream(it, null, sample) }
}

private fun openStream(context: android.content.Context, uri: Uri): InputStream? {
    if (uri.scheme == "file") {
        val path = uri.path ?: return null
        val file = File(path)
        if (!file.isFile) return null
        return file.inputStream()
    }
    return runCatching { context.contentResolver.openInputStream(uri) }.getOrNull()
}

private fun sampleSize(width: Int, height: Int, maxEdge: Int): Int {
    var sample = 1
    while (width / sample > maxEdge || height / sample > maxEdge) sample *= 2
    return sample
}
