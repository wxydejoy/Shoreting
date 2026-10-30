package cn.weiekko.dock.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class WeatherInfoTest {
    @Test
    fun pm25GradeMatchesHubTable() {
        assertEquals("优", WeatherInfo.pm25Grade(0.0))
        assertEquals("优", WeatherInfo.pm25Grade(34.9))
        assertEquals("良", WeatherInfo.pm25Grade(35.0))
        assertEquals("良", WeatherInfo.pm25Grade(74.0))
        assertEquals("轻度污染", WeatherInfo.pm25Grade(75.0))
        assertEquals("中度污染", WeatherInfo.pm25Grade(120.0))
        assertEquals("重度污染", WeatherInfo.pm25Grade(150.0))
        assertEquals("严重污染", WeatherInfo.pm25Grade(300.0))
    }

    @Test
    fun airLineAppearsOnlyWithPm25() {
        val withAir = WeatherInfo(city = "上海", celsius = 26.0, condition = "多云", pm25 = 42.4)
        assertEquals("空气良 42", withAir.airLine())
        val withoutAir = WeatherInfo(city = "上海", celsius = 26.0, condition = "多云")
        assertNull(withoutAir.airLine())
    }
}
