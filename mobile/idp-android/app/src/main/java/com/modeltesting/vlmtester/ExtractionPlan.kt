package com.modeltesting.vlmtester

/**
 * What a single-image scan needs, decided from the document type and the model: how the image is
 * prepared, which prompt, grammar and schema go in, how many tokens the answer may use, and how
 * the answer is checked. Pure, so every combination can be unit-tested; MainActivity does the
 * image work, runs the model and shows the result. Moved out of `runExtraction` unchanged; the
 * two-sided (front and back) flow is not covered here.
 */
object ExtractionPlan {
    /** How the picked image is prepared before it is sent to the model. */
    enum class ImagePrep { CANVAS, MINICPM, LFM2, QWEN_DOC, QWEN_PAN, QWEN, NONE }

    /** The document-type groups that change how a scan is run. They are defined by the activity. */
    data class DocTypeSets(
        val singleFieldCrop: Set<String>,
        val stage1: Set<String>,
        val skipSystemPromptOnly: Set<String>
    )

    /**
     * [promptAsset] and [grammarAsset] are asset paths for the caller to load. [sendsSchema] says
     * whether the document's JSON schema goes in. [validationDocumentType] is added to the answer
     * before the checks when the grammar leaves the `document_type` key out; null means the answer
     * is checked as it is. [isCrop] selects the single-field crop check instead of the full one.
     * [maxEdge] is the longest image edge, in pixels, for [ImagePrep.LFM2] and [ImagePrep.QWEN_DOC]; 0 for
     * every other plan.
     * [compact] means the short prompt and small grammar are used and only the fields of the
     * compact profile come back, so the checks grade only those.
     */
    data class Plan(
        val imagePrep: ImagePrep,
        val tokenCap: Int,
        val promptAsset: String,
        val trimPrompt: Boolean,
        val sendsSchema: Boolean,
        val useGrammar: Boolean,
        val useSystemPrompt: Boolean,
        val maxTokens: Int,
        val grammarAsset: String?,
        val validationDocumentType: String?,
        val isCrop: Boolean,
        val maxEdge: Int = 0,
        val compact: Boolean = false
    ) {
        val useCanvas: Boolean get() = imagePrep == ImagePrep.CANVAS
    }

    private const val PAN_FULL = "pan_full"
    private const val AADHAAR = "aadhaar"

    /** The ` | profile=...` suffixes the run log adds to its header for the validated profiles. */
    fun profileSuffix(docType: String, modelFileName: String): String =
        (if (ModelProfiles.useLfm2PanProfile(docType, modelFileName)) " | profile=lfm2-bul2-grammar" else "") +
            (
                if (ModelProfiles.useQwenPanProfile(docType, modelFileName)) {
                    " | profile=qwen-pan-oneline-${ModelProfiles.QWEN_PAN_CARD_EDGE}px"
                } else {
                    ""
                }
                ) +
            (
                if (ModelProfiles.useQwenAadhaarProfile(
                        docType,
                        modelFileName
                    )
                ) {
                    " | profile=qwen-aadhaar-flag-fullsize"
                } else {
                    ""
                }
                )

    fun forDocument(
        docType: String,
        modelFileName: String,
        sets: DocTypeSets,
        panFullHighCap: Boolean,
        useFieldCanvas: Boolean
    ): Plan {
        val isStage1 = sets.stage1.contains(docType)
        val isCrop = sets.singleFieldCrop.contains(docType)
        val skipJsonContract = isStage1 || isCrop
        val lfm2Pan = ModelProfiles.useLfm2PanProfile(docType, modelFileName)
        val qwenPan = ModelProfiles.useQwenPanProfile(docType, modelFileName)
        val qwenAadhaar = ModelProfiles.useQwenAadhaarProfile(docType, modelFileName)
        val lfm2Docs = ModelProfiles.useLfm2DocumentResize(docType, modelFileName)
        val compact = ModelProfiles.useCompactDocument(docType, modelFileName)
        val qwenDoc = ModelProfiles.useQwenDocumentResize(docType, modelFileName)
        return Plan(
            imagePrep = imagePrepFor(docType, modelFileName, lfm2Pan || lfm2Docs, qwenDoc, qwenPan, useFieldCanvas),
            tokenCap = tokenCapFor(docType, sets, panFullHighCap, lfm2Pan, qwenPan),
            promptAsset = promptAssetFor(docType, lfm2Pan, qwenAadhaar, compact),
            // trim(): the validated BUL2 runs fed the prompt through shell $(cat ...), which
            // drops the file's trailing newline.
            trimPrompt = lfm2Pan,
            sendsSchema = !skipJsonContract,
            useGrammar = !skipJsonContract,
            useSystemPrompt = !skipJsonContract && !sets.skipSystemPromptOnly.contains(docType) && !compact,
            maxTokens = maxTokensFor(docType, isStage1, isCrop),
            grammarAsset = grammarAssetFor(docType, lfm2Pan, qwenPan, qwenAadhaar, compact),
            validationDocumentType = validationDocumentTypeFor(docType, lfm2Pan || qwenPan, qwenAadhaar, compact),
            isCrop = isCrop,
            maxEdge = maxEdgeFor(lfm2Pan, lfm2Docs, qwenDoc),
            compact = compact
        )
    }

    private fun maxEdgeFor(lfm2Pan: Boolean, lfm2Docs: Boolean, qwenDoc: Boolean): Int = when {
        lfm2Pan -> ModelProfiles.LFM2_PAN_MAX_EDGE
        lfm2Docs -> ModelProfiles.LFM2_DOCUMENT_MAX_EDGE
        qwenDoc -> ModelProfiles.QWEN_DOCUMENT_MAX_EDGE
        else -> 0
    }

    private fun tokenCapFor(
        docType: String,
        sets: DocTypeSets,
        panFullHighCap: Boolean,
        lfm2Pan: Boolean,
        qwenPan: Boolean
    ): Int = when {
        lfm2Pan -> ModelProfiles.LFM2_PAN_IMAGE_TOKEN_CAP
        qwenPan -> ModelProfiles.QWEN_PAN_IMAGE_TOKEN_CAP
        else -> ModelProfiles.imageTokenCapFor(docType, sets.singleFieldCrop, panFullHighCap)
    }

    private fun promptAssetFor(docType: String, lfm2Pan: Boolean, qwenAadhaar: Boolean, compact: Boolean): String =
        when {
            lfm2Pan -> PromptFile.userAsset("pan_full_lfm2")
            compact -> PromptFile.userAsset("${docType}_compact")
            qwenAadhaar -> PromptFile.userAsset("aadhaar_qwen")
            else -> PromptFile.userAsset(docType)
        }

    private fun grammarAssetFor(
        docType: String,
        lfm2Pan: Boolean,
        qwenPan: Boolean,
        qwenAadhaar: Boolean,
        compact: Boolean
    ): String? = when {
        compact -> "grammars/${docType}_compact.gbnf"
        lfm2Pan -> "grammars/pan_full_lfm2.gbnf"
        qwenPan -> "grammars/pan_full_qwen.gbnf"
        qwenAadhaar -> "grammars/aadhaar_qwen.gbnf"
        else -> null
    }

    private fun validationDocumentTypeFor(
        docType: String,
        isPanProfile: Boolean,
        qwenAadhaar: Boolean,
        compact: Boolean
    ): String? = when {
        compact -> docType
        isPanProfile -> "pan"
        qwenAadhaar -> AADHAAR
        else -> null
    }

    private fun imagePrepFor(
        docType: String,
        modelFileName: String,
        lfm2Resize: Boolean,
        qwenDoc: Boolean,
        qwenPan: Boolean,
        useFieldCanvas: Boolean
    ): ImagePrep = when {
        useFieldCanvas && docType == PAN_FULL && ModelProfiles.isMiniCpm(modelFileName) -> ImagePrep.CANVAS
        ModelProfiles.isMiniCpm(modelFileName) -> ImagePrep.MINICPM
        lfm2Resize -> ImagePrep.LFM2
        qwenDoc -> ImagePrep.QWEN_DOC
        qwenPan -> ImagePrep.QWEN_PAN
        ModelProfiles.isQwen(modelFileName) -> ImagePrep.QWEN
        else -> ImagePrep.NONE
    }

    // pan_full's own schema (document_type/pan_number/name/parent_name/parent_relation/dob) only
    // ever needs ~70-150 tokens for a real answer (confirmed on-device 2026-09-29: a successful
    // MiniCPM-V extraction used exactly 73) - 512 was generous headroom for other models but let
    // MiniCPM-V's runaway thinking-mode decode burn the full budget before the
    // enable_thinking:false fix. Kept as a safety cap now that thinking is disabled, not the
    // primary fix for that.
    private fun maxTokensFor(docType: String, isStage1: Boolean, isCrop: Boolean): Int = when {
        isStage1 -> 1024
        isCrop -> 64
        docType == PAN_FULL -> 200
        else -> 512
    }
}
