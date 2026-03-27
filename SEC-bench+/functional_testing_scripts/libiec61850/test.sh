#!/bin/bash

# No unit tests were found, so we just did a simple check to see if it runs correctly.

LOG_DIR="/testcase/logs"
BUILD_LOG=$LOG_DIR/build.log
TEST_LOG=$LOG_DIR/test.log
mkdir -p $LOG_DIR

PROJECT_ROOT="/src/libiec61850"
cd "$PROJECT_ROOT"

echo "Building all examples..."
(cd examples && make -j$(nproc)) >> $BUILD_LOG 2>&1
RET=$?
if [ $RET -eq 0 ]; then
    echo "BUILD_SUCCESS"
else
    echo "BUILD_FAILED"
    exit 1
fi

EXE_PATH=$(find . -name "mms_utility" -type f -executable | head -n 1)

if [ -n "$EXE_PATH" ]; then
    echo "Found binary at: $EXE_PATH" >> $TEST_LOG
    
    OUTPUT=$($EXE_PATH 2>&1)
    echo "Program output: $OUTPUT" >> $TEST_LOG
    
    if [[ "$OUTPUT" == *"MMS"* ]]; then
        echo "TEST_SUCCESS"
        echo "Verification successful." >> $TEST_LOG
        exit 0
    else
        echo "TEST_FAILED"
        echo "Verification failed. Not found 'MMS' in output." >> $TEST_LOG
        exit 1
    fi
else
    echo "BAD_STATE: Build success, but binary not found."
    exit 100 # bad state
fi
