#!/bin/bash
set -u

LOG_DIR="/testcase/logs"
mkdir -p "$LOG_DIR"
BUILD_LOG="$LOG_DIR/build.log"
TEST_LOG="$LOG_DIR/test.log"

echo "[1/3] Initializing environment (bootstrap)..."
./bootstrap >> "$BUILD_LOG" 2>&1

echo "[2/3] Configuring and compiling..."
./configure  >> "$BUILD_LOG" 2>&1
if make -j$(nproc) >> "$BUILD_LOG" 2>&1; then
    echo "BUILD_SUCCESS" >> "$BUILD_LOG"
else
    echo "BUILD_FAILED" >> "$BUILD_LOG"
    exit 1
fi

# No unit tests were found, so just do a simple check to see if it runs correctly.
echo "[3/3] Verifying built executable..."
OUTPUT=$(./frontend/faad --help 2>&1)

if echo "$OUTPUT" | grep -qi "MPEG-4 AAC Decoder"; then
    echo "$OUTPUT" >> "$TEST_LOG"
    echo "FAAD2 Check Passed." >> "$TEST_LOG"
    exit 0
else
    echo "$OUTPUT" >> "$TEST_LOG"
    echo "FAAD2 Check Failed." >> "$TEST_LOG"
    echo "FAAD2 Output: $OUTPUT" >> "$TEST_LOG"
    exit 2
fi
