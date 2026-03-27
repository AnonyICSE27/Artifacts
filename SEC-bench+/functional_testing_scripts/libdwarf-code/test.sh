#!/bin/bash
set -u

SRC_DIR=$(pwd)
BUILD_DIR="$SRC_DIR/build_tmp"
LOG_DIR="/testcase/logs"
mkdir -p "$LOG_DIR"
BUILD_LOG="$LOG_DIR/build.log"
TEST_LOG="$LOG_DIR/test.log"

echo "[1/3] Cleaning..."
rm -rf "$BUILD_DIR" >> "$BUILD_LOG" 2>&1

echo "[2/3] Configuring and Building..."

if [ -f "./autogen.sh" ]; then
    sh ./autogen.sh >> "$BUILD_LOG" 2>&1
else
    echo "Error: autogen.sh not found in $(pwd)" >> "$BUILD_LOG"
    exit 1
fi

mkdir -p "$BUILD_DIR" && cd "$BUILD_DIR"

"$SRC_DIR/configure" --enable-shared --disable-dependency-tracking >> "$BUILD_LOG" 2>&1

if [ $? -ne 0 ]; then
    echo "Configuration failed." >> "$BUILD_LOG"
    exit 1
fi

if make -j$(nproc) >> "$BUILD_LOG" 2>&1; then
    echo "Build Successful."
else
    echo "Build Failed." >> "$BUILD_LOG"
    exit 1
fi

echo "[3/3] Running tests..."
if make check -j$(nproc) >> "$TEST_LOG" 2>&1; then
    echo "KO: 0"
else
    echo "KO: 1"
fi
