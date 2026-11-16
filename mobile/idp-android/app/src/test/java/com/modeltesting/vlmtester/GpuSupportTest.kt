package com.modeltesting.vlmtester

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [GpuSupport]. */
class GpuSupportTest {
    private val shippedLibraries = listOf(
        "libggml-base.so", "libggml-cpu.so", "libggml.so", "libllama-common.so", "libllama-mtmd-cli.so",
        "libllama-server-impl.so", "libllama-server.so", "libllama.so", "libmtmd.so", "libomp.so"
    )

    @Test
    fun `the GPU is available only when a Vulkan backend library is present`() {
        assertFalse(GpuSupport.isAvailable(shippedLibraries))
        assertTrue(GpuSupport.isAvailable(shippedLibraries + "libggml-vulkan.so"))
    }

    @Test
    fun `an empty library folder has no GPU`() {
        assertFalse(GpuSupport.isAvailable(emptyList()))
    }

    @Test
    fun `other backends do not count as the Vulkan backend`() {
        assertFalse(GpuSupport.isAvailable(listOf("libggml-cpu.so", "libggml-opencl.so", "libggml-cuda.so")))
    }

    @Test
    fun `the label says the build is CPU only`() {
        assertTrue(GpuSupport.UNAVAILABLE_LABEL.contains("CPU only"))
    }

    @Test
    fun `the switch in the layout still exists for the activity to disable`() {
        val layout = File("src/main/res/layout/activity_main.xml").readText(Charsets.UTF_8)
        assertTrue(layout.contains("@+id/switchGpu"))
        assertEquals(1, Regex("@\\+id/switchGpu").findAll(layout).count())
    }
}
