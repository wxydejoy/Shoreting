package cn.weiekko.dock.ui

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private val Ink = Color(0xFF0E0E0E)
private val Panel = Color(0xFF181818)
private val Ivory = Color(0xFFE8E4DC)
private val Mute = Color(0xFF8E8A82)
private val Accent = Color(0xFFD4C4A8)
private val Danger = Color(0xFFE8A0A0)

private val DockColors = darkColorScheme(
    primary = Accent,
    onPrimary = Ink,
    primaryContainer = Color(0xFF2A261C),
    onPrimaryContainer = Ivory,
    background = Ink,
    onBackground = Ivory,
    surface = Panel,
    onSurface = Ivory,
    surfaceVariant = Color(0xFF222222),
    onSurfaceVariant = Mute,
    outline = Color(0xFF333333),
    error = Danger,
    onError = Ink,
    errorContainer = Color(0xFF2A1C1C),
    onErrorContainer = Danger,
)

private val DockTypography = Typography(
    displayLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.ExtraLight,
        fontSize = 112.sp,
        lineHeight = 112.sp,
        letterSpacing = (-4).sp,
        color = Ivory,
    ),
    headlineMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Light,
        fontSize = 32.sp,
        lineHeight = 36.sp,
        letterSpacing = (-0.6).sp,
        color = Ivory,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 18.sp,
        lineHeight = 24.sp,
        color = Ivory,
    ),
    bodyLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        lineHeight = 22.sp,
        color = Mute,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 20.sp,
        color = Mute,
    ),
    bodySmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 13.sp,
        lineHeight = 18.sp,
        color = Mute,
    ),
    labelMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 12.sp,
        letterSpacing = 0.8.sp,
        color = Mute,
    ),
)

private val DockShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(22.dp),
    large = RoundedCornerShape(28.dp),
)

/** F36 节日皮肤：只动 accent 和背景的色温，字重字号一律不碰——
 *  皮肤是「今天长得不一样」，不是「今天换了个 App」。 */
private fun skinColorScheme(skin: String) = when (skin) {
    // 春节 / 元旦：宣纸换朱砂
    "spring_festival", "new_year" -> DockColors.copy(
        primary = Color(0xFFE8A0A0),
        primaryContainer = Color(0xFF2A1C1C),
        background = Color(0xFF120D0D),
        surface = Color(0xFF1D1717),
    )
    // 圣诞：松枝绿配旧金
    "christmas" -> DockColors.copy(
        primary = Color(0xFFB8C4A0),
        primaryContainer = Color(0xFF20261C),
        background = Color(0xFF0D100D),
        surface = Color(0xFF161A16),
    )
    // 生日：旧金换蜜糖
    "birthday" -> DockColors.copy(
        primary = Color(0xFFE8C88C),
        primaryContainer = Color(0xFF2A2418),
    )
    // 考试周：收着点，冷灰蓝，少点火气
    "exam_week" -> DockColors.copy(
        primary = Color(0xFFA8B8C4),
        primaryContainer = Color(0xFF1C2226),
    )
    // 下雨天：青灰
    "rainy" -> DockColors.copy(
        primary = Color(0xFF9FB4C4),
        primaryContainer = Color(0xFF1A2228),
        background = Color(0xFF0C0E10),
        surface = Color(0xFF141719),
    )
    else -> DockColors
}

@Composable
fun DockTheme(skin: String = "", content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (skin.isBlank()) DockColors else skinColorScheme(skin),
        typography = DockTypography,
        shapes = DockShapes,
        content = content,
    )
}
