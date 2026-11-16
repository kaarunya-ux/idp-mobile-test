package com.modeltesting.vlmtester

import org.json.JSONObject

/** Builds a JSONObject from pairs; a null value becomes JSON null. */
internal fun json(vararg pairs: Pair<String, Any?>): JSONObject {
    val obj = JSONObject()
    for ((key, value) in pairs) obj.put(key, value ?: JSONObject.NULL)
    return obj
}
