#!/bin/bash
cd /src/yara

# --- Core cleanup steps ---
echo "🧹 Cleaning environment..."
# Attempting to clean old build results
[ -f Makefile ] && make distclean > /dev/null 2>&1
# Delete previous log files to prevent test.py from reading old data
rm -f build.log test.log test_result.json

# --- Rebuild steps ---
echo "🔨 Starting Fresh Build..."
./bootstrap.sh > build.log 2>&1
./configure >> build.log 2>&1
make -j$(nproc) >> build.log 2>&1
MAKE_RET=$?

# Verify compilation
if [ $MAKE_RET -eq 0 ] ; then
    echo " Build Successful"
else
    echo " Build Failed"
    exit 1
fi

# --- Automated testing ---
echo " Running Official Test Suite..."
# Use official recommended test command
make check -j$(nproc) > test.log 2>&1
CHECK_RET=$?

if [ $CHECK_RET -eq 0 ]; then
    echo " All Tests Passed"
    exit 0
else
    echo " Tests Failed"
    exit 2
fi