package com.modeltesting.vlmtester

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** JVM unit tests for [ImageSizing]. Expected values were computed independently of the Kotlin code. */
class ImageSizingTest {
    // --- resizePlan -----------------------------------------------------------------------

    @Test
    fun `resizePlan shrinks a camera photo to the longest edge and sub-samples while it can`() {
        assertEquals(ImageSizing.ResizePlan(560, 350, 4), ImageSizing.resizePlan(4000, 2502, 560))
        assertEquals(ImageSizing.ResizePlan(560, 420, 2), ImageSizing.resizePlan(1280, 960, 560))
        assertEquals(ImageSizing.ResizePlan(480, 300, 8), ImageSizing.resizePlan(4000, 2502, 480))
    }

    @Test
    fun `resizePlan leaves an image alone when it is already within the limit`() {
        assertNull(ImageSizing.resizePlan(500, 300, 560))
        assertNull(ImageSizing.resizePlan(560, 300, 560))
        assertNull(ImageSizing.resizePlan(1200, 800, 1280))
    }

    @Test
    fun `resizePlan leaves an image alone when its size could not be read`() {
        assertNull(ImageSizing.resizePlan(0, 100, 560))
        assertNull(ImageSizing.resizePlan(100, 0, 560))
        assertNull(ImageSizing.resizePlan(-1, -1, 560))
    }

    @Test
    fun `resizePlan keeps the existing 32-bit float rounding`() {
        // 448 / 3000 in 32-bit float truncates to 447. This is how the app has always behaved.
        assertEquals(ImageSizing.ResizePlan(447, 447, 4), ImageSizing.resizePlan(3000, 3000, 448))
    }

    // --- qwenTargetEdge -------------------------------------------------------------------

    @Test
    fun `qwenTargetEdge shrinks a photo that holds more than twice the pixel budget`() {
        assertEquals(915, ImageSizing.qwenTargetEdge(4000, 2502, tokenCap = 256))
        assertEquals(1831, ImageSizing.qwenTargetEdge(4000, 2502, tokenCap = 1024))
    }

    @Test
    fun `qwenTargetEdge passes a card-sized image through untouched`() {
        assertNull(ImageSizing.qwenTargetEdge(860, 538, tokenCap = 256))
    }

    @Test
    fun `qwenTargetEdge with a headroom of one shrinks to the exact budget`() {
        assertEquals(647, ImageSizing.qwenTargetEdge(860, 538, tokenCap = 256, headroom = 1.0))
    }

    @Test
    fun `qwenTargetEdge ignores an image whose size could not be read`() {
        assertNull(ImageSizing.qwenTargetEdge(0, 5, tokenCap = 256))
    }

    // --- qwenPanTargetEdge ----------------------------------------------------------------

    @Test
    fun `qwenPanTargetEdge sends a tight card crop at the fixed card width`() {
        assertEquals(480, ImageSizing.qwenPanTargetEdge(860, 538, tokenCap = 256))
        assertEquals(480, ImageSizing.qwenPanTargetEdge(900, 500, tokenCap = 256))
    }

    @Test
    fun `qwenPanTargetEdge treats an image over the crop limit as a camera photo`() {
        assertEquals(480, ImageSizing.qwenPanTargetEdge(1000, 600, tokenCap = 256))
        assertEquals(661, ImageSizing.qwenPanTargetEdge(1001, 600, tokenCap = 256))
        assertEquals(647, ImageSizing.qwenPanTargetEdge(4000, 2502, tokenCap = 256))
    }

    @Test
    fun `qwenPanTargetEdge ignores an image whose size could not be read`() {
        assertNull(ImageSizing.qwenPanTargetEdge(0, 0, tokenCap = 256))
    }
}
