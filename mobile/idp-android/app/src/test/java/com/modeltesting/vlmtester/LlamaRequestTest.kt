package com.modeltesting.vlmtester

import org.json.JSONException
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [LlamaRequest]. The payload is read back with a JSON parser, so key order does not matter. */
class LlamaRequestTest {
    private fun scan(
        useSystemPrompt: Boolean = true,
        foldSystemPrompt: Boolean = false,
        imageFirst: Boolean = false,
        grammarText: String? = null,
        jsonSchema: String? = null,
        thinkingOff: Boolean = true
    ) = LlamaRequest.Scan(
        systemPrompt = "SYS",
        promptText = "READ THE CARD",
        imageBase64 = "QUJD",
        useSystemPrompt = useSystemPrompt,
        foldSystemPrompt = foldSystemPrompt,
        imageFirst = imageFirst,
        maxTokens = 512,
        grammarText = grammarText,
        jsonSchema = jsonSchema,
        thinkingOff = thinkingOff
    )

    private val imageUrl = "data:image/jpeg;base64,QUJD"

    // --- scanPayload ------------------------------------------------------------------------

    @Test
    fun `scanPayload sends a system message, then the instructions, then the image`() {
        val payload = LlamaRequest.scanPayload(scan())
        val messages = payload.getJSONArray("messages")
        assertEquals(2, messages.length())
        assertEquals("system", messages.getJSONObject(0).getString("role"))
        assertEquals("SYS", messages.getJSONObject(0).getString("content"))
        val user = messages.getJSONObject(1)
        assertEquals("user", user.getString("role"))
        val parts = user.getJSONArray("content")
        assertEquals("text", parts.getJSONObject(0).getString("type"))
        assertEquals("READ THE CARD", parts.getJSONObject(0).getString("text"))
        assertEquals("image_url", parts.getJSONObject(1).getString("type"))
        assertEquals(imageUrl, parts.getJSONObject(1).getJSONObject("image_url").getString("url"))
    }

    @Test
    fun `scanPayload sets deterministic sampling and the token limit`() {
        val payload = LlamaRequest.scanPayload(scan())
        assertEquals(0, payload.getInt("temperature"))
        assertEquals(1.1, payload.getDouble("repeat_penalty"), 0.0)
        assertEquals(512, payload.getInt("max_tokens"))
    }

    @Test
    fun `scanPayload folds the system prompt into the user text when told to`() {
        val messages = LlamaRequest.scanPayload(scan(foldSystemPrompt = true)).getJSONArray("messages")
        assertEquals(1, messages.length())
        val text = messages.getJSONObject(0).getJSONArray("content").getJSONObject(0).getString("text")
        assertEquals("SYS\n\nREAD THE CARD", text)
    }

    @Test
    fun `scanPayload sends no system prompt at all when it is switched off`() {
        for (fold in listOf(false, true)) {
            val messages = LlamaRequest.scanPayload(scan(useSystemPrompt = false, foldSystemPrompt = fold))
                .getJSONArray("messages")
            assertEquals(1, messages.length())
            val text = messages.getJSONObject(0).getJSONArray("content").getJSONObject(0).getString("text")
            assertEquals("READ THE CARD", text)
        }
    }

    @Test
    fun `scanPayload can put the image before the instructions`() {
        val parts = LlamaRequest.scanPayload(scan(imageFirst = true)).getJSONArray("messages")
            .getJSONObject(1).getJSONArray("content")
        assertEquals("image_url", parts.getJSONObject(0).getString("type"))
        assertEquals("text", parts.getJSONObject(1).getString("type"))
    }

    @Test
    fun `scanPayload prefers a raw grammar over a JSON schema`() {
        val payload = LlamaRequest.scanPayload(scan(grammarText = "root ::= x", jsonSchema = "{\"type\":\"object\"}"))
        assertEquals("root ::= x", payload.getString("grammar"))
        assertFalse(payload.has("json_schema"))
    }

    @Test
    fun `scanPayload sends the JSON schema as an object when there is no grammar`() {
        val payload = LlamaRequest.scanPayload(scan(jsonSchema = "{\"type\":\"object\"}"))
        assertEquals("object", payload.getJSONObject("json_schema").getString("type"))
        assertFalse(payload.has("grammar"))
    }

    @Test
    fun `scanPayload sends no output constraint for a blank or missing schema`() {
        for (schema in listOf(null, "", "   ")) {
            val payload = LlamaRequest.scanPayload(scan(jsonSchema = schema))
            assertFalse(schema.toString(), payload.has("json_schema"))
            assertFalse(schema.toString(), payload.has("grammar"))
        }
    }

    @Test
    fun `scanPayload switches thinking off unless told not to`() {
        val on = LlamaRequest.scanPayload(scan(thinkingOff = true))
        assertFalse(on.getJSONObject("chat_template_kwargs").getBoolean("enable_thinking"))
        assertFalse(LlamaRequest.scanPayload(scan(thinkingOff = false)).has("chat_template_kwargs"))
    }

    // --- warmUpPayload ----------------------------------------------------------------------

    @Test
    fun `warmUpPayload has the shape of a real Qwen scan with a short output`() {
        val payload = LlamaRequest.warmUpPayload("SYS", "READ THE CARD", "QUJD", "root ::= x")
        val messages = payload.getJSONArray("messages")
        assertEquals("system", messages.getJSONObject(0).getString("role"))
        val parts = messages.getJSONObject(1).getJSONArray("content")
        assertEquals("READ THE CARD", parts.getJSONObject(0).getString("text"))
        assertEquals(imageUrl, parts.getJSONObject(1).getJSONObject("image_url").getString("url"))
        assertEquals(24, payload.getInt("max_tokens"))
        assertEquals("root ::= x", payload.getString("grammar"))
        assertEquals(0, payload.getInt("temperature"))
        assertEquals(1.1, payload.getDouble("repeat_penalty"), 0.0)
        assertFalse(payload.getJSONObject("chat_template_kwargs").getBoolean("enable_thinking"))
    }

    @Test
    fun `warmUpPayload leaves out the system message when there is none`() {
        val messages = LlamaRequest.warmUpPayload(null, "READ", "QUJD", "g").getJSONArray("messages")
        assertEquals(1, messages.length())
        assertEquals("user", messages.getJSONObject(0).getString("role"))
    }

    // --- usageSummary -----------------------------------------------------------------------

    @Test
    fun `usageSummary reports token counts and timings`() {
        val body = JSONObject()
            .put("usage", JSONObject().put("prompt_tokens", 400).put("completion_tokens", 24))
            .put("timings", JSONObject().put("cache_n", 110).put("prompt_ms", 1234.9).put("predicted_ms", 567.2))
            .toString()
        assertEquals(
            "prompt_tokens=400 completion_tokens=24 cache_n=110 prompt_ms=1234 predicted_ms=567",
            LlamaRequest.usageSummary(body)
        )
    }

    @Test
    fun `usageSummary falls back to unavailable and to -1 for missing fields`() {
        assertEquals("usage: unavailable", LlamaRequest.usageSummary("{}"))
        assertEquals("usage: unavailable", LlamaRequest.usageSummary("not json"))
        assertEquals(
            "prompt_tokens=-1 completion_tokens=7",
            LlamaRequest.usageSummary("""{"usage":{"completion_tokens":7}}""")
        )
        assertEquals(
            "usage: unavailable cache_n=-1 prompt_ms=-1 predicted_ms=-1",
            LlamaRequest.usageSummary("""{"timings":{}}""")
        )
    }

    // --- contentOf --------------------------------------------------------------------------

    @Test
    fun `contentOf returns the first choice's message content`() {
        val body = """{"choices":[{"message":{"content":"{\"name\":\"A\"}"}},{"message":{"content":"other"}}]}"""
        assertEquals("{\"name\":\"A\"}", LlamaRequest.contentOf(body))
    }

    @Test
    fun `contentOf fails with a JSON error when the reply has no answer`() {
        for (body in listOf("{}", """{"choices":[]}""", "not json")) {
            var failed = false
            try {
                LlamaRequest.contentOf(body)
            } catch (_: JSONException) {
                failed = true
            }
            assertTrue(body, failed)
        }
    }
}
