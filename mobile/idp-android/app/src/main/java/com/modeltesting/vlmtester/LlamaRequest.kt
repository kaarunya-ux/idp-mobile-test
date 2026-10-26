package com.modeltesting.vlmtester

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

/**
 * The JSON the app sends to `llama-server`'s OpenAI-style `/v1/chat/completions` endpoint, and the
 * reading of its reply. Pure (strings in, JSON out), so the request shape can be unit-tested; the
 * HTTP call itself stays in MainActivity. Moved unchanged. The caller decides, from the model file
 * name, whether the system prompt is folded into the user text, whether the image goes first, and
 * whether thinking is switched off, so nothing here knows about a particular model.
 */
object LlamaRequest {
    /** One extraction request. [jsonSchema] is used only when [grammarText] is null and it is not blank. */
    data class Scan(
        val systemPrompt: String,
        val promptText: String,
        val imageBase64: String,
        val useSystemPrompt: Boolean,
        val foldSystemPrompt: Boolean,
        val imageFirst: Boolean,
        val maxTokens: Int,
        val grammarText: String?,
        val jsonSchema: String?,
        val thinkingOff: Boolean
    )

    fun scanPayload(scan: Scan): JSONObject = JSONObject().apply {
        put("messages", messagesOf(scan))
        put("temperature", 0)
        // Matches the CLI path's --repeat-penalty 1.1 (llama.cpp defaults this to
        // 1.0/disabled - confirmed in common/common.h - so it's genuinely off unless
        // set here explicitly).
        put("repeat_penalty", 1.1)
        put("max_tokens", scan.maxTokens)
        putOutputConstraint(scan)
        // MiniCPM-V 4.6's chat template defaults to thinking mode ("thinking = 1" in
        // the server's own startup log) - confirmed on-device 2026-09-29: every
        // pan_full request hit the full max_tokens=512 ceiling with the model still
        // inside its <think> block, so the actual JSON answer either barely made it
        // out (slow but valid) or got truncated entirely ("no JSON object found"),
        // depending on how long that image's reasoning ran. server-common.cpp reads
        // this as a JSON bool (via .dump(), not a string) and overrides the
        // template's default per-request. Sent unconditionally - harmless for models
        // whose template doesn't support thinking, and there's no reason any of these
        // structured-extraction doc types would want reasoning tokens anyway.
        // Not sent for LFM2: its template has no thinking mode, and the validated
        // server runs did not send it.
        if (scan.thinkingOff) put("chat_template_kwargs", JSONObject().put("enable_thinking", false))
    }

    private fun messagesOf(scan: Scan): JSONArray {
        val messages = JSONArray()
        if (scan.useSystemPrompt && !scan.foldSystemPrompt) {
            messages.put(
                JSONObject().apply {
                    put("role", "system")
                    put("content", scan.systemPrompt)
                }
            )
        }
        val effectivePrompt = if (scan.foldSystemPrompt && scan.useSystemPrompt) {
            "${scan.systemPrompt}\n\n${scan.promptText}"
        } else {
            scan.promptText
        }
        val textPart = JSONObject().apply {
            put("type", "text")
            put("text", effectivePrompt)
        }
        val imagePart = imagePart(scan.imageBase64)
        // LFM2-VL: image FIRST, instructions after - the order every validated LFM2 result
        // used. Instructions-first lost a field (22/25 vs 23/25, an invented PAN on card a)
        // and saved no time: this server build cached 1 token of ~190 either way.
        val content = if (scan.imageFirst) {
            JSONArray().put(imagePart).put(textPart)
        } else {
            JSONArray().put(textPart).put(imagePart)
        }
        messages.put(
            JSONObject().apply {
                put("role", "user")
                put("content", content)
            }
        )
        return messages
    }

    /** A raw grammar wins; otherwise a non-blank JSON schema drives the same constrained decoding. */
    private fun JSONObject.putOutputConstraint(scan: Scan) {
        if (scan.grammarText != null) {
            put("grammar", scan.grammarText)
        } else if (!scan.jsonSchema.isNullOrBlank()) {
            put("json_schema", JSONObject(scan.jsonSchema))
        }
    }

    /**
     * The throwaway request that warms a freshly started server: exactly the shape of a real
     * Qwen scan (system message only when asked for, instructions first, then the image), so the
     * identical instruction prefix is left in the server's cache. Output is discarded, so it is
     * kept short.
     */
    fun warmUpPayload(systemPrompt: String?, userText: String, imageBase64: String, grammar: String): JSONObject {
        val content = JSONArray()
            .put(JSONObject().put("type", "text").put("text", userText))
            .put(imagePart(imageBase64))
        val messages = JSONArray()
        if (systemPrompt != null) messages.put(JSONObject().put("role", "system").put("content", systemPrompt))
        messages.put(JSONObject().put("role", "user").put("content", content))
        return JSONObject().apply {
            put("messages", messages)
            put("temperature", 0)
            put("repeat_penalty", 1.1)
            put("max_tokens", WARM_UP_MAX_TOKENS)
            put("grammar", grammar)
            put("chat_template_kwargs", JSONObject().put("enable_thinking", false))
        }
    }

    /**
     * One-line usage figures from the reply, for the run log. prompt_tokens is dominated by the
     * vision tokens produced by the image preprocessor, so it is the only direct, per-run
     * measurement of how much image the model actually ingested. cache_n is how many prompt tokens
     * the server reused instead of recomputing (the instruction text, ~110 on a warm PAN scan);
     * prompt_ms and predicted_ms split the request into reading and writing time. Absent on an
     * error body, hence the "unavailable" fallback.
     */
    fun usageSummary(body: String): String {
        var summary = "usage: unavailable"
        try {
            val reply = JSONObject(body)
            val usage = reply.optJSONObject("usage")
            if (usage != null) {
                val promptTokens = usage.optInt("prompt_tokens", -1)
                val completionTokens = usage.optInt("completion_tokens", -1)
                summary = "prompt_tokens=$promptTokens completion_tokens=$completionTokens"
            }
            val timings = reply.optJSONObject("timings")
            if (timings != null) {
                summary += " cache_n=${timings.optInt("cache_n", -1)}" +
                    " prompt_ms=${timings.optDouble("prompt_ms", -1.0).toLong()}" +
                    " predicted_ms=${timings.optDouble("predicted_ms", -1.0).toLong()}"
            }
        } catch (_: JSONException) {
            // keep whatever was read before the body stopped making sense
        }
        return summary
    }

    /** The model's answer: `choices[0].message.content`. Throws JSONException if the reply has no such field. */
    fun contentOf(body: String): String = JSONObject(body)
        .getJSONArray("choices").getJSONObject(0)
        .getJSONObject("message").getString("content")

    private fun imagePart(imageBase64: String): JSONObject = JSONObject().apply {
        put("type", "image_url")
        put("image_url", JSONObject().put("url", "data:image/jpeg;base64,$imageBase64"))
    }

    private const val WARM_UP_MAX_TOKENS = 24
}
