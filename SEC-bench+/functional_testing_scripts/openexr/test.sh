#!/bin/bash

# ==============================================================================
# Script function: OpenEXR automated clean, build and test
# Return codes:
#   0 - Build succeeded and all tests passed
#   1 - Build phase failed
#   2 - Build succeeded but some tests failed
# ==============================================================================
LOG_DIR="/testcase/logs"
mkdir -p "$LOG_DIR"
TEST_LOG="$LOG_DIR/test.log"
BUILD_LOG="$LOG_DIR/build.log"

# 1. [Cleanup]
echo "[1/3] 🧹 Cleaning..."
# if [ -f Makefile ]; then
#     make clean >> "$BUILD_LOG" 2>&1
# fi
# make clean is not enough
git clean -f -d

# 2. [Build]
echo "[2/3] 🔨 Building..."
# Configure OpenEXR (enable tests)
cmake . -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_TESTING=ON \
        -DOPENEXR_BUILD_BOTH_STATIC_SHARED=OFF >> "$BUILD_LOG" 2>&1
RET=$?
if [ $RET -ne 0 ]; then
    echo " CMake Configuration Failed!" >> "$BUILD_LOG"
    exit 101
fi

# Perform compilation
if ! make -j$(nproc) >> "$BUILD_LOG" 2>&1; then
    echo "Build Failed"
    exit 1
fi
echo "Build Successful"

# 3. [Run] Running tests
echo "[3/3]  Running Tests..."
# Use ctest to run OpenEXR unit tests
ctest --output-on-failure > "$TEST_LOG" 2>&1
RET=$?

# 4. [Evaluate]
if [ $RET -eq 0 ]; then
    echo "All Test Passed."
    exit 0
else
    echo "Some Test Failed."
    exit 2
fi