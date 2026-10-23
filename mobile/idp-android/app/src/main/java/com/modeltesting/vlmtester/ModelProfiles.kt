package com.modeltesting.vlmtester

/**
 * Which on-device model is loaded and which settings each document type gets with it.
 * Pure decisions, no Android state, so they run in plain JVM unit tests. Models are
 * recognised by their file name. Every number here was measured on a phone; the comments
 * keep the evidence, and ModelProfilesTest pins the values so a change shows up in review.
 *
 * Moved out of MainActivity without changing behaviour; MainActivity keeps thin delegates
 * with the same names.
 */
object ModelProfiles {
    // MiniCPM-V 4.6 ships a real custom Jinja chat template (confirmed via its GGUF's
    // tokenizer.chat_template metadata, not plain ChatML), so in server mode it needs --jinja
    // and a folded system prompt. Its mtmd projector type is "minicpmv4_6" - a distinct
    // preprocessing path from VisionPsy's idefics3 - so it is kept as its own family.
    fun isMiniCpm(fileName: String): Boolean = fileName.contains("minicpm", ignoreCase = true)

    /** LFM2-VL-3B (Liquid AI), Q4_K_M LM + Q8_0 mmproj. Its image path is NOT tiled below
     *  512x512 and its token count follows --image-max-tokens smoothly (about 0.15s per
     *  image token on this phone), so it gets its own settings instead of the generic ones:
     *  128 image tokens, -c 4096 (the GGUF's default context is 128,000), the spaced
     *  single-line grammar and the bullet prompt (assets/grammars/pan_full_lfm2.gbnf and
     *  assets/prompts/user_pan_full_lfm2.v1.md).
     *  All of it measured on-device 2026-09-30 and written up in
     *  LFM2_VL_3B_LATENCY_FINDINGS.md: 24/25 fields on the five test cards at 39.7s
     *  cold, 23/25 on full-card images at 35.7s cold / 33.3s warm server. */
    fun isLfm2(fileName: String): Boolean = fileName.contains("lfm2", ignoreCase = true)

    /** Qwen3-VL (2B and 4B). Its GGUF also declares a 262,144-token context, so with no -c
     *  llama.cpp's auto-fit picks one from free memory: on this 7.6 GB phone the 4B got a
     *  26,368-token context (about 3.9 GB of KV cache beside 2.5 GB of weights; 39.6 GB
     *  projected at the full default) and took 111.5s against 83-99s with -c 4096 on the
     *  same card and settings, adjacent runs. Heat moved the same configuration by about
     *  8s, so the real saving is somewhere in 12-28s. With -c 4096 the process held about
     *  3.5 GB resident (2.3 GB of mapped weights, 1.2 GB heap). Measured 2026-10-06 in the
     *  adb harness on the 4B; the 2B was not re-measured. Real prompts here are well under
     *  4096 tokens even for the 1024-image-token doc types (about 1024 + 600 text + 512
     *  output). */
    fun isQwen(fileName: String): Boolean = fileName.contains("qwen", ignoreCase = true)

    /** pan_full + LFM2-VL only - the one doc type whose LFM2 prompt/grammar are validated. */
    fun useLfm2PanProfile(docType: String, fileName: String): Boolean = docType == "pan_full" && isLfm2(fileName)

    /** Image-token cap for the LFM2 pan_full profile. 196 -> 128 -> 64 gave 55 -> 44 -> 31s,
     *  but 64 lost parent_name/parent_relation (card e), so 128 is the floor that kept
     *  accuracy. The app's 128/256 pan_full toggle does not apply: 256 was never tested on
     *  LFM2 and would add ~0.15s per extra token (~+19s). */
    const val LFM2_PAN_IMAGE_TOKEN_CAP = 128

    /** The Indian identity documents, besides PAN, whose image is shrunk before LFM2-VL reads it. */
    private val lfm2ResizedDocuments = setOf("aadhaar", "dl", "passport")

    /** LFM2-VL on aadhaar, dl or passport: the image is shrunk to [LFM2_DOCUMENT_MAX_EDGE]. */
    fun useLfm2DocumentResize(docType: String, fileName: String): Boolean =
        isLfm2(fileName) && docType in lfm2ResizedDocuments

    /** Longest image edge sent to LFM2-VL for pan_full. llama.cpp 000bee5 (upstream #27057) cuts
     *  an image into 512px tiles of 256 tokens each when its area exceeds twice the pixel budget
     *  of the token cap; at a cap of 128 that is 262,144 px, and an 860x538 card (468,000 px)
     *  became 15 tiles, about 3,800 tokens and 85 to 95+ s. At 500px the card stays one image of
     *  112 tokens. Measured 2026-10-22 on five cards, new libraries, app flags: 22 to 26 s and
     *  24/25 fields at 500px, 23 to 27 s but misread digits at 400px, 24 to 26 s and the same
     *  fields at 600px (closer to the tiling limit). Command-line test with the image resized by
     *  sips, not yet run through the app's own resize. See
     *  docs/mobile-extraction/ON_DEVICE_LATENCY_AND_ACCURACY_FINDINGS.md. */
    const val LFM2_PAN_MAX_EDGE = 500

    /** Longest image edge sent to LFM2-VL for aadhaar, dl and passport. These images used to go
     *  at full size (many reach 800 to 1,000 tokens, and a 2743x1904 photo tiled to 2,589).
     *  Measured 2026-10-23, warm server, compact prompt, 560px: DL 23 to 29 s (13/15 fields),
     *  passport 25 to 29 s (30/34), Aadhaar 14 to 22 s (19/24); at full size the large cards took
     *  81 s to over 170 s. The accuracy cost against full size is not measured for LFM2 (for
     *  Qwen on passport it was 33/34 to 28/34), and small print may blur. */
    const val LFM2_DOCUMENT_MAX_EDGE = 560

    private val compactDocuments = setOf("aadhaar", "dl", "passport")

    /** aadhaar, dl and passport with LFM2-VL, MiniCPM-V or Qwen3-VL: a short prompt, no system
     *  prompt, and a small grammar that fixes the answer (assets/prompts/user_<doc>_compact.v1.md,
     *  assets/grammars/<doc>_compact.gbnf) instead of the long prompt and the full JSON schema.
     *  The full DL prompt plus system prompt is about 800 tokens and its answer 146 tokens; on the
     *  Galaxy M35 the in-app LFM2 DL scan took 63.5 s (1,075 prompt tokens, 43.6 s to read them).
     *  Measured 2026-10-23 (warm server, resized image, compact prompt): DL 23 to 29 s on LFM2,
     *  Qwen 29 to 46 s and MiniCPM 23 to 25 s, against 50+ s and 33 to 43 s with the full prompt.
     *  The answer carries only the fields the tests read: DL licence number, name, DOB, issue date,
     *  NT validity; passport number, name, DOB, gender, place of birth, issue and expiry dates;
     *  Aadhaar number, name, DOB, gender. Address, blood group, organ donor, vehicle classes, TR
     *  validity, issuing authority, nationality, PIN and address are not asked for. Aadhaar with
     *  Qwen keeps its own profile (useQwenAadhaarProfile). Other model families keep the full
     *  prompt. The checks grade only these fields (DlRules, PassportRules, AadhaarRules). */
    fun useCompactDocument(docType: String, fileName: String): Boolean = when {
        docType !in compactDocuments -> false
        docType == "aadhaar" -> (isLfm2(fileName) || isMiniCpm(fileName)) && !isQwen(fileName)
        else -> isLfm2(fileName) || isMiniCpm(fileName) || isQwen(fileName)
    }

    /** pan_full + Qwen3-VL only: warm server + a one-line GBNF (assets/grammars/pan_full_qwen.gbnf)
     *  + a card image sent at 480px. On the 4B (Q4_K_M + Q8_0 mmproj) in the adb harness,
     *  2026-10-06, on the four cards of pan_test_images: 44.6s average (41.4-50.3), 20/20 fields,
     *  against 82-85s for the one-shot CLI at full size. The pieces, each measured on its own:
     *  the warm server reuses the 110-token instruction prefix and skips the model load
     *  (about 85s -> 73s), the one-line grammar saves output tokens, and 480px sends about
     *  135 image tokens instead of 240. 448px (about 98-120 tokens) was 41.0s but misread the
     *  PAN on the smallest card, so do not go lower. 512px was 50.1s, 20/20, with more margin -
     *  change qwenPanCardEdge if 480 proves too thin on real scans. The image-token cap
     *  stays at the 256 baseline and ignores the 128/256 toggle (see lfm2PanImageTokenCap). */
    fun useQwenPanProfile(docType: String, fileName: String): Boolean = docType == "pan_full" && isQwen(fileName)

    const val QWEN_PAN_IMAGE_TOKEN_CAP = 256
    const val QWEN_PAN_CARD_EDGE = 480

    /** The 480px rule is only validated on tight card crops (the four test cards are 600-860px
     *  on the long edge, the card filling the frame). A camera photo with the card at 30-50% of
     *  the frame would shrink the card to a few hundred pixels of unreadable print, and the
     *  grammar would then force a PAN-shaped answer anyway. Anything larger than this is treated
     *  as a camera photo and takes the old, validated path (downscaleForQwen, 256 tokens). A
     *  stopgap until the card is detected and cropped before resizing. */
    const val QWEN_PAN_CROP_MAX_EDGE = 1000

    /** Aadhaar + Qwen3-VL: the whole picture at full size (no shrinking - fewer image tokens
     *  costs accuracy on the small print), a one-line GBNF (assets/grammars/aadhaar_qwen.gbnf),
     *  and a prompt that makes the model state explicitly whether an address block is printed
     *  (assets/prompts/user_aadhaar_qwen.v1.md). The grammar ties the flag to the answer:
     *  address_visible=true forces a real address string (15+ chars) and an optional PIN;
     *  false forces address and pincode to null. Mac benchmark 2026-10-07/08, 6 test cards,
     *  same 4B model: today's request (schema, no flag) 31/36 fields and the model returned a
     *  null address on 2 cards that have one; one-line grammar alone 31/36 (same misses); flag
     *  + new prompt 35/36 with the flag right on all 6 cards. The one miss is a name read
     *  "Jasjit" for "Jasjot", which reproduces in every configuration (the model's own reading).
     *  The address-or-null decision is borderline and flipped between the Mac and the phone on
     *  one card, so this must be confirmed on the phone. Image-token cap stays at the 1024 that
     *  imageTokenCapFor() gives every non-PAN type: the tallest test card (about 1,600 tokens
     *  at full size) is capped to roughly 1,000 and still read 6/6 on the Mac. */
    fun useQwenAadhaarProfile(docType: String, fileName: String): Boolean = docType == "aadhaar" && isQwen(fileName)

    /** One server cap for every Qwen doc type, so switching between PAN and Aadhaar never
     *  restarts the server (the cap is a start-up setting, see capAffectsServer). Sizing is
     *  done in the app per doc type instead: the PAN profile sends a 480px card, or a photo
     *  pre-sized to the 256-token budget (downscaleForQwenPan), so this ceiling is not binding
     *  for it, and Aadhaar needs up to 1024. */
    const val QWEN_SERVER_IMAGE_TOKEN_CAP = 1024

    /** pan_full only: manual switch (switchPanFullHighCap in the UI, default ON) instead
     *  of an earlier per-image blur-detection approach (Laplacian-variance sharpness
     *  scoring) tried and abandoned the same day. On-device testing 2026-09-24 found 256
     *  fixes every real misread seen at 128 on CLEAN cards too, not just blurred ones
     *  (SOHRAAB DANISH: "SOHRAB"/"AZFAL" -> correct; SAROJINI M: a wrong PAN digit ->
     *  correct) - so 128 was marginal for dense print regardless of blur, which is why
     *  blur-detection was replaced with this simpler manual toggle instead of kept as an
     *  "auto" option. Off drops back to 128 for when speed matters more than the ~10-15s
     *  accuracy premium. Every other doc type keeps its fixed cap unchanged. */
    fun imageTokenCapFor(docType: String, singleFieldCropDocTypes: Set<String>, panFullHighCap: Boolean): Int = when {
        singleFieldCropDocTypes.contains(docType) -> 256
        docType == "pan_full" -> if (panFullHighCap) 256 else 128
        else -> 1024
    }
}
