package com.modeltesting.vlmtester

/**
 * Whether the native libraries shipped with the app contain a GPU backend. The shipped llama.cpp
 * build is CPU only (the Vulkan build was dropped because it gave poor results), so the
 * "Use GPU (Vulkan)" switch must not pretend otherwise: with no GPU backend present, asking for
 * the GPU would silently run on the CPU while the run log called it a GPU run. The Python
 * Standard says never to fall back silently (ML-10). Pure, so it runs in plain JVM unit tests.
 */
object GpuSupport {
    private const val VULKAN_BACKEND_PREFIX = "libggml-vulkan"

    /** True if [libraryFileNames], the files in the app's native library folder, include the Vulkan backend. */
    fun isAvailable(libraryFileNames: Collection<String>): Boolean =
        libraryFileNames.any { it.startsWith(VULKAN_BACKEND_PREFIX) }

    /** The text for the switch when the GPU is not available. */
    const val UNAVAILABLE_LABEL = "GPU (Vulkan) is not in this build - runs on the CPU only"
}
