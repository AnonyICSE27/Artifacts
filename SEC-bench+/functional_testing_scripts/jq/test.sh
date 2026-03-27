#!/bin/bash

cd /src/jq

# 1. Environment Preparation
echo "🧹 Cleaning and updating submodules..."
git submodule update --init --recursive > /dev/null 2>&1
RET=$?
if [ $RET -ne 0 ]; then
    echo " Submodule update failed. Check Network."
    exit 101
fi

rm -f build.log test.log

# 2. Compilation
echo "🔨 Starting Build..."
autoreconf -i > build.log 2>&1
./configure --with-oniguruma=builtin >> build.log 2>&1
make -j$(nproc) >> build.log 2>&1

# As long as jq binary exists, consider Compilation successful
if [ ! -f "./jq" ]; then
    echo " Build Failed"
    exit 1
fi

echo " Build Successful"

# 3. Run tests (no determination)
echo " Running Official Tests..."
make check > test.log 2>&1 || true

# Regardless of test result, leave to test.py to determine
exit 0
