#!/bin/bash
set -u

cd /src/libsndfile
LOG_DIR="/testcase/logs"
mkdir -p "$LOG_DIR"
BUILD_LOG="$LOG_DIR/build.log"
TEST_LOG="$LOG_DIR/test.log"

echo "[1/3] Bootstrapping..."
[ -f Makefile ] && make distclean >> "$BUILD_LOG" 2>&1 || true

./autogen.sh >> "$BUILD_LOG" 2>&1

echo "[2/3] Configuring and Building..."
./configure --disable-shared >> "$BUILD_LOG" 2>&1

if make -j$(nproc) >> "$BUILD_LOG" 2>&1; then
    echo "Build Successful."
else
    echo "Build Failed."
    exit 1
fi

echo "[3/3] Running tests..."
if make check -j$(nproc) >> "$TEST_LOG" 2>&1; then
    echo "All tests passed." >> "$TEST_LOG"
    echo "KO: 0" >> "$TEST_LOG"
    exit 0
else
    echo "Tests failed." >> "$TEST_LOG"
    echo "KO: 1" >> "$TEST_LOG"
    exit 2
fi
