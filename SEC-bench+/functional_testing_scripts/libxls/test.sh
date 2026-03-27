#!/bin/bash
cd /src/libxls

# 1. Environment Preparation and Cleanup
echo "🧹 Cleaning and bootstrapping..."
rm -f build.log test.log
if [ -f Makefile ]; then
    make clean >> build.log 2>&1
fi
./bootstrap >> build.log 2>&1

# 2. Configuration and Compilation
echo "🔨 Starting Build..."
./configure >> build.log 2>&1
make -j$(nproc) >> build.log 2>&1
MAKE_RET=$?

# Check build artifact (executable xls2csv)
if [ $MAKE_RET -eq 0 ] && [ -f "./xls2csv" ]; then
    echo " Build Successful"
else
    echo " Build Failed"
    exit 1
fi

# 3. Run official test suite (make check)
echo " Running Tests..."
make check -j$(nproc) >> test.log 2>&1
CHECK_RET=$?


if [ $CHECK_RET -eq 0 ]; then
    echo " All Tests Passed"
    exit 0
else
    echo " Tests Failed"
    exit 2
fi