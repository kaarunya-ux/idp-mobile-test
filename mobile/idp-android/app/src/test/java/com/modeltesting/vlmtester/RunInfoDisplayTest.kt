package com.modeltesting.vlmtester

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class RunInfoDisplayTest {
    private val header = "[passport | CPU only | server mode | model=minicpm-v-4.6-q4_k_m.gguf | " +
        "mmproj=mmproj-minicpm-v-4.6-q8_0.gguf | threads=4 | registryModel=minicpm-v-4.6@4.6 | " +
        "runtime=llama.cpp | sha256=b1a5aa76b5ef | cap=1024 | promptVersion=user_passport_compact.v1 | 30.1s]"
    private val diag = "img=420x560 server=WARM(reused, no model load) prompt_tokens=292 completion_tokens=96 " +
        "cache_n=0 prompt_ms=20412 predicted_ms=9608 total_wall=30.1s prep=downscale 0.02s total_incl_prep=30.1s"

    @Test
    fun `lists the processor, model, settings, image, tokens and timing`() {
        val expected = listOf(
            "Document: passport",
            "Processor: CPU only",
            "Mode: server mode",
            "Model: minicpm-v-4.6-q4_k_m.gguf",
            "Model id: minicpm-v-4.6@4.6 b1a5aa76b5ef",
            "Threads: 4",
            "Image-token limit (max): 1024",
            "Prompt: user_passport_compact.v1",
            "Image: 420x560, prep downscale 0.02 s",
            "Image tokens (estimate): about 229",
            "Server: WARM(reused, no model load)",
            "Tokens: 292 prompt, 96 answer",
            "Time: read 20.4 s, write 9.6 s, total 30.1 s"
        ).joinToString("\n")
        assertEquals(expected, RunInfoDisplay.format(header, diag))
    }

    @Test
    fun `leaves out what the text does not contain and shows cached tokens`() {
        val out = RunInfoDisplay.format("[dl | 5.0s]", "prompt_tokens=100 completion_tokens=5 cache_n=54")
        assertFalse(out.contains("Model"))
        val expected = "Document: dl\nMode: one-shot (model loaded for this scan)\n" +
            "Tokens: 100 prompt, 5 answer, 54 reused from cache\nTime: total 5.0 s"
        assertEquals(expected, out)
    }

    @Test
    fun `never estimates more image tokens than the limit`() {
        val out = RunInfoDisplay.format("[dl | cap=100 | 5.0s]", "img=2000x2000")
        assertEquals(true, out.contains("Image tokens (estimate): about 100"))
    }
}
