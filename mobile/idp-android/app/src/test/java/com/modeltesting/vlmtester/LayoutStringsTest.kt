package com.modeltesting.vlmtester

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests that keep the screen's texts in `strings.xml` and its images described. */
class LayoutStringsTest {
    private val layout = File("src/main/res/layout/activity_main.xml").readText(Charsets.UTF_8)
    private val strings = File("src/main/res/values/strings.xml").readText(Charsets.UTF_8)
    private val stringNames = Regex("<string name=\"([^\"]+)\"").findAll(strings).map { it.groupValues[1] }.toList()

    @Test
    fun `no text in the layout is written into it directly`() {
        val literals = Regex("android:text=\"([^\"]*)\"").findAll(layout).map { it.groupValues[1] }
            .filterNot { it.startsWith("@string/") }.toList()
        assertEquals(emptyList<String>(), literals)
    }

    @Test
    fun `every string the layout refers to exists and is not empty`() {
        val references = Regex("@string/([A-Za-z0-9_]+)").findAll(layout).map { it.groupValues[1] }.toSet()
        assertTrue(references.isNotEmpty())
        for (name in references) {
            assertTrue(name, name in stringNames)
            assertTrue(name, Regex("<string name=\"$name\"[^>]*>\"(.+)\"</string>").containsMatchIn(strings))
        }
    }

    @Test
    fun `string names are unique`() {
        assertEquals(stringNames.size, stringNames.toSet().size)
    }

    @Test
    fun `every image in the layout has a description`() {
        val images = Regex("<ImageView[^>]*>", RegexOption.DOT_MATCHES_ALL).findAll(layout).map { it.value }.toList()
        assertTrue(images.isNotEmpty())
        for (image in images) {
            assertTrue(image, image.contains("android:contentDescription=\"@string/"))
        }
    }
}
