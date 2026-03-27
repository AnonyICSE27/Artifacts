#!/bin/bash
cd /src/libmodbus

# 1. Environment Cleanup and Initialization
echo "🧹 Cleaning environment..."
rm -f build.log test.log
if [ -f Makefile ]; then
    make clean >> build.log 2>&1
fi
# if [ ! -f "./configure" ]; then
./autogen.sh >> build.log 2>&1
# fi

# 2. Configuration and Compilation
echo "🔨 Starting Build..."
./configure >> build.log 2>&1
make -j$(nproc) >> build.log 2>&1
MAKE_RET=$?

# Check if library file is generated
if [ $MAKE_RET -eq 0 ]; then
    echo " Build Successful"
else
    echo " Build Failed"
    exit 1
fi

# 3. Run tests
echo " Running Unit Tests..."
# libmodbus supports running make check directly
make check > test.log 2>&1
TEST_RET=$?

if [ $TEST_RET -eq 0 ]; then
    echo " All Tests Passed"
    exit 0
else
    echo " Tests Failed (Check test.log for details)"
    exit 2
fi