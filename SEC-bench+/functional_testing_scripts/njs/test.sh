#!/bin/bash

LOG_DIR="/testcase/logs"
mkdir -p "$LOG_DIR"
TEST_LOG="$LOG_DIR/test.log"
BUILD_LOG="$LOG_DIR/build.log"

echo "[1/3] Cleaning..."
make clean > /dev/null 2>&1 || true
rm -rf build/ >> "$BUILD_LOG" 2>&1

echo "[2/3] Building..."
./configure >> "$BUILD_LOG" 2>&1
if ! make -j$(nproc) >> "$BUILD_LOG" 2>&1; then
    echo "Build Failed! Check $BUILD_LOG"
    exit 1
fi
echo "Build Successful."

echo "[3/3] Running Unit Tests..."
make test -j$(nproc) > /dev/null 2>&1 # just used to compile the unit tests
# 'test/test262 --binary=build/njs' is failed even in the benchmark-provided patched version.
# we only run the unit tests.
./build/njs_unit_test > "$TEST_LOG" 2>&1 # run the unit tests
RET=$?

if [ $RET -eq 0 ]; then
    echo "Unit tests passed!" >> "$TEST_LOG"
    echo "KO: 0" >> "$TEST_LOG"
    echo "Test Passed. Returning 0."
    exit 0
else
    echo "Unit tests failed!" >> "$TEST_LOG"
    echo "KO: 1" >> "$TEST_LOG"
    echo "Test Failed (Exit Code: $RET). Returning 2."
    exit 2
fi
