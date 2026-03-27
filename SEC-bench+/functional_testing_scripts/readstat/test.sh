#!/bin/bash
cd /src/readstat

# 1. Environment Preparation and Cleanup
echo "🧹 Cleaning and bootstrapping..."
rm -f build.log test.log
if [ -f Makefile ]; then
    make clean >> build.log 2>&1
fi
./autogen.sh >> build.log 2>&1

# 2. Configuration and Compilation
echo "🔨 Starting Build..."
./configure >> build.log 2>&1
make -j$(nproc) >> build.log 2>&1
MAKE_RET=$?

# Check build artifact readstat executable
if [ $MAKE_RET -eq 0 ]; then
    echo " Build Successful"
else
    echo " Build Failed"
    exit 1
fi

# 3. Run official test suite (make check)
# ReadStat test suite verifies parsing accuracy of various .dta, .sav files
echo " Running Official Tests..."
make check -j$(nproc) > test.log 2>&1
CHECK_RET=$?

if [ $CHECK_RET -eq 0 ]; then
    echo " All Tests Passed"
    exit 0
else
    echo " Tests Failed"
    exit 2
fi