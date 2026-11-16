package com.modeltesting.vlmtester

/** Rules for the model files the user pushes to the phone: which are models, which are projectors. */
object ModelFiles {
    /** The two lists the model spinners show, each sorted by name. */
    data class Choices(val languageModels: List<String>, val projectors: List<String>)

    private const val PROJECTOR_PREFIX = "mmproj-"

    /** Splits the `.gguf` files into language models and `mmproj-` projectors; other files are ignored. */
    fun choices(fileNames: List<String>): Choices {
        val gguf = fileNames.filter { it.endsWith(".gguf") }
        return Choices(
            languageModels = gguf.filter { !it.startsWith(PROJECTOR_PREFIX) }.sorted(),
            projectors = gguf.filter { it.startsWith(PROJECTOR_PREFIX) }.sorted()
        )
    }

    /**
     * Whether the projector belongs to the model (4B with 4B, 2B with 2B): the model name up to
     * "-Instruct" must appear in the projector name. A mismatched pair cannot load.
     */
    fun projectorMatches(modelName: String, projectorName: String): Boolean =
        projectorName.contains(modelName.substringBefore("-Instruct"), ignoreCase = true)
}
