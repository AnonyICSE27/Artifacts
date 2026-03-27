#!/bin/bash

# ==============================================================================
# Script function: MATIO Automated cleanup, compilation and testing
# Return code definition:
#   0 - Compilation successful and all tests passed
#   1 - Compilation failed
#   2 - Compilation successful but tests failed
# ==============================================================================

LOG_DIR="/testcase/logs"
mkdir -p "$LOG_DIR"
BUILD_LOG="$LOG_DIR/build.log"
TEST_LOG="$LOG_DIR/test.log"

# 1. [Environment Prep] Install necessary dependencies (Ubuntu/Debian example)
echo "[1/4] 📦 Installing dependencies..."
# if command -v apt-get >/dev/null; then
apt-get update >> "$BUILD_LOG" 2>&1
apt-get install -y zlib1g-dev libhdf5-dev automake autoconf libtool git >> "$BUILD_LOG" 2>&1
# fi
RET=$?
if [ $RET -ne 0 ]; then
    echo " Dependency installation failed. Check Network."
    exit 100
fi

if [ ! -d "test/datasets" ]; then
    git clone https://git.code.sf.net/p/matio/matio_test_datasets test/datasets --depth 1
    RET=$?
    if [ $RET -ne 0 ]; then
        echo " Clone requirement repos failed. Check Network."
        exit 102
    fi
fi

# 2. [Cleanup and Preparation]
echo "[2/4] 🧹 Cleaning..."
# If build directory exists clean it, matio usually supports in-place or directory compilation
make distclean > /dev/null 2>&1 || true

# 3. [Compilation] Execute Autotools build process
echo "[3/4] 🔨 Building MATIO..."
./autogen.sh >> "$BUILD_LOG" 2>&1
# Enable HDF5 and zlib support
./configure --enable-mat73=yes --with-zlib --with-hdf5 >> "$BUILD_LOG" 2>&1

if [ $? -ne 0 ]; then
    echo " Configuration Failed!" >> "$BUILD_LOG"
    exit 1
fi

if ! make -j$(nproc) >> "$BUILD_LOG" 2>&1; then
    echo " Build Failed!" >> "$BUILD_LOG"
    exit 1
fi
echo " Build Successful."

# 4. [Testing] Run bundled testsuite
echo "[4/4]  Running Unit Tests..."
# make check is Autotools standard test command
make check -j$(nproc) > "$TEST_LOG" 2>&1
RET=$?

# Determination result
if [ $RET -eq 0 ]; then
    echo "All tests passed!" >> "$TEST_LOG"
    echo "🎉 Test Passed. Returning 0."
    exit 0
else
    echo "Some tests failed! Check $TEST_LOG" >> "$BUILD_LOG"
    echo " Test Failed. Returning 2."
    exit 2
fi