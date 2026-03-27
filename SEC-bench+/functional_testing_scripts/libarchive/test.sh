#!/bin/bash

LOG_DIR="/testcase/logs"
mkdir -p "$LOG_DIR"
BUILD_LOG="$LOG_DIR/build.log"
TEST_LOG="$LOG_DIR/test.log"

echo "[1/3] Cleaning up old artifacts..."
if [ -f Makefile ]; then
    make clean >> "$BUILD_LOG" 2>&1 || true
fi
#rm -rf build/

sh build/autogen.sh >> "$BUILD_LOG" 2>&1
./configure --disable-shared >> "$BUILD_LOG" 2>&1

echo "[2/3] Building..."
if make -j$(nproc) >> "$BUILD_LOG" 2>&1; then
    echo "BUILD_SUCCESS"
else
    echo "BUILD_FAILED"
    exit 1
fi

echo "[3/3] Testing..."
make check -j$(nproc) >> "$TEST_LOG" 2>&1
RET=$?
if [ $RET -eq 0 ]; then
    echo "TEST_SUCCESS"
else
    echo "TEST_FAILED"
    exit 1
fi
