#!/usr/bin/env bash
# Build the llama.cpp native libraries the app runs: Android arm64, ARMv8.2 with dot-product and
# fp16, OpenMP on, Vulkan off, shared libraries. Everything else mirrors the app's shipped build.
#
# The build is pinned (Python Engineering Standard: nothing unpinned, never "latest"): llama.cpp is
# checked out at one exact commit, and the NDK and CMake versions are fixed. Nothing is tied to one
# person's machine; every location below can be overridden with an environment variable.
#
#   mobile/idp-android/native/build_llama_dotprod.sh            # build
#   DRY_RUN=1 mobile/idp-android/native/build_llama_dotprod.sh  # check the setup, build nothing
#
# Variables (defaults in brackets):
#   LLAMA_CPP_COMMIT   commit to build   [000bee54a544070356b44d68af299b973d391d26]
#   LLAMA_CPP_REPO     where to clone    [https://github.com/ggml-org/llama.cpp]
#   NDK_VERSION        Android NDK       [27.1.12297006]
#   CMAKE_VERSION      SDK CMake         [4.1.2]
#   ANDROID_HOME       Android SDK       [$ANDROID_SDK_ROOT, else ~/Library/Android/sdk]
#   LLAMA_BUILD_DIR    work folder       [~/llama_dotprod_build] (outside the repository)
#   BUILD_TARGETS      CMake targets     [llama-server llama-bench]
#   JOBS               parallel jobs     [8]
#   DRY_RUN            1 = only check    [0]
# NOTE (2026-10-22): libraries built from this commit tile LFM2 images (upstream PR #27057), which makes
# LFM2 about 3x slower on the phone than the old libraries unless the input is resized to about 500 px.
# Qwen3-VL and MiniCPM-V are not affected. See docs/mobile-extraction/STANDARDIZATION_HANDOFF.md, section 5.
set -euo pipefail

# 000bee5 (2026-10-08) is the commit recorded in libllama-common.so and libggml-base.so of the
# libraries the app shipped with, and the one the original build logs printed.
LLAMA_CPP_COMMIT="${LLAMA_CPP_COMMIT:-000bee54a544070356b44d68af299b973d391d26}"
LLAMA_CPP_REPO="${LLAMA_CPP_REPO:-https://github.com/ggml-org/llama.cpp}"
NDK_VERSION="${NDK_VERSION:-27.1.12297006}"
CMAKE_VERSION="${CMAKE_VERSION:-4.1.2}"
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
WORK_DIR="${LLAMA_BUILD_DIR:-$HOME/llama_dotprod_build}"
# The original script built these two targets only. The app's one-shot path also runs
# llama-mtmd-cli; add it here (BUILD_TARGETS="llama-server llama-bench llama-mtmd-cli") to build it.
BUILD_TARGETS="${BUILD_TARGETS:-llama-server llama-bench}"
JOBS="${JOBS:-8}"
DRY_RUN="${DRY_RUN:-0}"

fail() { echo "ERROR: $*" >&2; exit 1; }

NDK="$SDK/ndk/$NDK_VERSION"
CMAKE="$SDK/cmake/$CMAKE_VERSION/bin/cmake"
NINJA="$SDK/cmake/$CMAKE_VERSION/bin/ninja"

[[ "$LLAMA_CPP_COMMIT" =~ ^[0-9a-f]{7,40}$ ]] || fail "LLAMA_CPP_COMMIT must be 7 to 40 hex digits, got '$LLAMA_CPP_COMMIT'"
[ -d "$SDK" ] || fail "Android SDK not found at $SDK (set ANDROID_HOME)"
[ -d "$NDK" ] || fail "Android NDK $NDK_VERSION not found at $NDK (install it with: sdkmanager \"ndk;$NDK_VERSION\")"
[ -x "$CMAKE" ] || fail "CMake $CMAKE_VERSION not found at $CMAKE (install it with: sdkmanager \"cmake;$CMAKE_VERSION\")"
[ -x "$NINJA" ] || fail "Ninja not found at $NINJA (it comes with the SDK CMake $CMAKE_VERSION)"
PREBUILT="$(ls -d "$NDK"/toolchains/llvm/prebuilt/* 2>/dev/null | head -1 || true)"
[ -n "$PREBUILT" ] || fail "no prebuilt toolchain under $NDK/toolchains/llvm/prebuilt"
command -v git > /dev/null || fail "git is required"

echo "=== plan"
echo "llama.cpp   $LLAMA_CPP_REPO @ $LLAMA_CPP_COMMIT"
echo "NDK         $NDK"
echo "CMake       $CMAKE"
echo "work folder $WORK_DIR"
echo "targets     $BUILD_TARGETS (-j $JOBS)"
if [ "$DRY_RUN" = "1" ]; then
  echo "DRY_RUN=1: setup is fine, nothing was built"
  exit 0
fi

mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

echo "=== checkout $(date +%H:%M:%S)"
if [ ! -d src/.git ]; then
  git clone --quiet --filter=blob:none --no-checkout "$LLAMA_CPP_REPO" src || fail "clone failed"
fi
if ! git -C src cat-file -e "${LLAMA_CPP_COMMIT}^{commit}" 2> /dev/null; then
  git -C src fetch --quiet origin || fail "fetch failed"
fi
git -C src checkout --quiet --detach "$LLAMA_CPP_COMMIT" || fail "commit $LLAMA_CPP_COMMIT not found in $LLAMA_CPP_REPO"
ACTUAL_COMMIT="$(git -C src rev-parse HEAD)"
case "$ACTUAL_COMMIT" in
  "$LLAMA_CPP_COMMIT"*) ;;
  *) fail "checked out $ACTUAL_COMMIT, expected $LLAMA_CPP_COMMIT" ;;
esac
echo "commit: $ACTUAL_COMMIT $(git -C src log -1 --format='%ad' --date=short)"

echo "=== toolchain check"
"$PREBUILT/bin/clang" --version | head -1 || fail "NDK clang does not run"

echo "=== configure $(date +%H:%M:%S)"
"$CMAKE" -S src -B build -G Ninja -DCMAKE_MAKE_PROGRAM="$NINJA" \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-28 -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16 -DGGML_OPENMP=ON -DGGML_VULKAN=OFF -DGGML_OPENCL=OFF \
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_SERVER=ON \
  -DBUILD_SHARED_LIBS=ON > configure.log 2>&1 \
  && echo "configure ok" || { echo "CONFIGURE FAILED"; tail -15 configure.log; exit 1; }

echo "=== build $(date +%H:%M:%S)"
# shellcheck disable=SC2086  # BUILD_TARGETS is a space-separated list on purpose
"$CMAKE" --build build --target $BUILD_TARGETS -j "$JOBS" > build.log 2>&1 \
  && echo "build ok" || { echo "BUILD FAILED"; grep -E "error|Error" build.log | head -8; exit 1; }

# A record of exactly what was built, to keep next to the libraries.
cat > build-info.txt << INFO
llama.cpp commit: $ACTUAL_COMMIT
repository:       $LLAMA_CPP_REPO
NDK:              $NDK_VERSION
CMake:            $CMAKE_VERSION
targets:          $BUILD_TARGETS
android:          arm64-v8a, android-28, armv8.2-a+dotprod+fp16, OpenMP on, Vulkan off
built:            $(date -u +%Y-%m-%dT%H:%M:%SZ)
INFO

echo "=== done $(date +%H:%M:%S)"
ls build/bin | head -20
echo "build info: $WORK_DIR/build-info.txt"
echo ALL_DONE
