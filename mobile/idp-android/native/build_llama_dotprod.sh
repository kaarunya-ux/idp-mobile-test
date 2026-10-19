#!/bin/bash
# Build upstream llama.cpp (server + bench) for Android arm64 with dot-product + fp16 enabled. Everything else mirrors the app's current library (shared libs, OpenMP).
B=/Users/impacto/Downloads/app_build/llama_dotprod; SDK=/Users/impacto/Library/Android/sdk; NDK=$SDK/ndk/27.1.12297006; CM=$SDK/cmake/4.1.2/bin/cmake; NJ=$SDK/cmake/4.1.2/bin/ninja
cd $B
echo "=== clone $(date +%H:%M:%S)"; [ -d src ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp src || { echo "CLONE FAILED"; exit 1; }
echo "commit: $(git -C src log -1 --format='%h %ad' --date=short)"
echo "=== toolchain check"; $NDK/toolchains/llvm/prebuilt/darwin-x86_64/bin/clang --version | head -1 || { echo "NDK CLANG DOES NOT RUN"; exit 1; }
echo "=== configure $(date +%H:%M:%S)"
$CM -S src -B build -G Ninja -DCMAKE_MAKE_PROGRAM=$NJ -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-28 -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16 -DGGML_OPENMP=ON -DGGML_VULKAN=OFF -DGGML_OPENCL=OFF \
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_SERVER=ON -DBUILD_SHARED_LIBS=ON > configure.log 2>&1 \
  && echo "configure ok" || { echo "CONFIGURE FAILED"; tail -15 configure.log; exit 1; }
echo "=== build $(date +%H:%M:%S)"
$CM --build build --target llama-server llama-bench -j 8 > build.log 2>&1 && echo "build ok" || { echo "BUILD FAILED"; grep -E "error|Error" build.log | head -8; exit 1; }
echo "=== done $(date +%H:%M:%S)"; ls build/bin | head -20; echo ALL_DONE
