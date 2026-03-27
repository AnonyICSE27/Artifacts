#!/bin/bash
cd /src/libjpeg-turbo

# 1. Clean environment
echo "🧹 Cleaning environment..."
rm -f build.log test.log
if [ -f Makefile ]; then
    make clean >> build.log 2>&1
fi

# 2. Compilation Phase
echo "🔨 Starting Build with CMake..."
cmake -G "Unix Makefiles" >> build.log 2>&1
make -j$(nproc) >> build.log 2>&1
MAKE_RET=$?

if [ $MAKE_RET -eq 0 ]; then
    echo " Build Successful"
else
    echo " Build Failed"
    exit 1
fi

# 3. Run official test suite
echo " Running Official Tests..."
ctest >> test.log 2>&1
TEST_RET=$?

if [ $TEST_RET -eq 0 ]; then
    echo " All Tests Passed"
    exit 0
else
    echo " Tests Failed"
    exit 2
fi