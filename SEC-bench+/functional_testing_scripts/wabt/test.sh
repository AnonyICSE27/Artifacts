#!/bin/bash

cd /src/wabt
LOG_DIR="/testcase/logs"
mkdir -p "$LOG_DIR"
BUILD_LOG="$LOG_DIR/build.log"
TEST_LOG="$LOG_DIR/test.log"

echo "[1/4] Cleaning build directory..."
rm -rf build/ >> "$BUILD_LOG" 2>&1

echo "[2/4] Checking submodules (gtest)..."
git submodule update --init --recursive >> "$BUILD_LOG" 2>&1

echo "[3/4] Configuring and Building..."
mkdir build && cd build

cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTS=ON >> "$BUILD_LOG" 2>&1

cmake --build . -j$(nproc) >> "$BUILD_LOG" 2>&1

if [ $? -ne 0 ]; then
    echo "Build Failed! Check $BUILD_LOG"
    exit 1
fi

echo "[4/4] Running tests..."
make check -j$(nproc) >> "$TEST_LOG" 2>&1

if [ $? -eq 0 ]; then
    echo "SUCCESS: Tests passed." >> "$TEST_LOG"
    echo "KO: 0"
    exit 0
else
    echo "FAILURE: Tests failed." >> "$TEST_LOG"
    echo "KO: 1"
    exit 2
fi
