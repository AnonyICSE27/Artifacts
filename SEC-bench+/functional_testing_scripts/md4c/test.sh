#!/bin/bash

# ==============================================================================
# Script function: MD4C Automated cleanup, compilation and testing (adapt to directory structure)
# Return code definition:
#   0 - Compilation successful and all tests passed
#   1 - Compilation failed
#   2 - Compilation successful but tests failed
# ==============================================================================

LOG_DIR="/testcase/logs"
mkdir -p "$LOG_DIR"
BUILD_LOG="$LOG_DIR/build.log"
TEST_LOG="$LOG_DIR/test.log"

# 1. [Cleanup]
echo "[1/3] 🧹 Cleaning..."
# Clean old build artifacts
rm -rf build/ >> "$BUILD_LOG" 2>&1
mkdir -p build

# 2. [Compilation]
echo "[2/3] 🔨 Building..."
cd build
# Configure project: Use CMake to generate build files
if ! cmake .. -DCMAKE_BUILD_TYPE=Release >> "$BUILD_LOG" 2>&1; then
    echo " CMake Configuration Failed!" >> "$BUILD_LOG"
    exit 1
fi

# Execute compilation: MD4C depends only on standard C library, build is very fast
if ! make -j$(nproc) >> "$BUILD_LOG" 2>&1; then
    echo " Build Failed! Check $BUILD_LOG"
    exit 1
fi
echo " Build Successful."

# 3. [Run] Execute core test cases
echo "[3/3]  Running Tests..."
# Key fix: Must call root directory script via relative path inside build directory
# So script can find binary in subdirectory md2html/
if [ -f "../scripts/run-tests.sh" ]; then
    bash ../scripts/run-tests.sh > "$TEST_LOG" 2>&1
    RET=$?
fi

# 4. [Determination]
if [ $RET -eq 0 ]; then
    echo "KO: 0" >> "$TEST_LOG"
    echo "🎉 Test Passed. Returning 0."
    exit 0
else
    echo "KO: 1" >> "$TEST_LOG"
    echo " Test Failed (Exit Code: $RET). Returning 2."
    exit 2
fi