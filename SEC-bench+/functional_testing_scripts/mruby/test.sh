#!/bin/bash

LOG_DIR=/testcase/logs
mkdir -p $LOG_DIR

echo "[1] clean build"
make clean > $LOG_DIR/build.log 2>&1 || true

echo "[2] build project"
# export CFLAGS="-fsanitize=address -g"
# export LDFLAGS="-fsanitize=address"
unset LD
make >> $LOG_DIR/build.log 2>&1 
# || rake >> $LOG_DIR/build.log 2>&1
BUILD_RET=$?

if [ $BUILD_RET -ne 0 ]; then
    echo "BUILD FAILED"
    exit 1
fi

echo "[3] run tests"
# export ASAN_OPTIONS=detect_leaks=0:abort_on_error=0
script -c "rake all test" > $LOG_DIR/test.log 2>&1  # Using `script` to make some cases happy
# script -c"rake test" /dev/null
TEST_RET=$?

if [ $TEST_RET -ne 0 ]; then
    echo "TEST FAILED"
    exit 2
fi

echo "ALL TEST PASSED"
exit 0
