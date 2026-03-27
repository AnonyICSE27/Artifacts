#!/bin/bash
cd /src/qpdf

# 1. Environment Preparation
echo "🧹 Cleaning and configuring..."
rm -f build.log test.log
# If no configure script (cloned from git), need to generate
if [ ! -f "./configure" ]; then
    autoreconf -i > build.log 2>&1
fi

# 2. Configuration and Compilation
echo "🔨 Starting Build..."
# Enable showing failed test output, convenient for integration environment debugging
./configure --enable-show-failed-test-output --disable-fuzzing >> build.log 2>&1

# QPDF fuzzing directory causes Compilation failed, temporarily rename to avoid
mv fuzz fuzz_bak

make -j$(nproc) >> build.log 2>&1
MAKE_RET=$?

# Check build artifact qpdf
if [ $MAKE_RET -eq 0 ]; then
    echo " Build Successful"
else
    echo " Build Failed"
    exit 1
fi

# 3. Run official test suite (make check)
echo " Running Official Tests..."
# QPDF test suite is very comprehensive
make check -j$(nproc) > test.log 2>&1
CHECK_RET=$?

if [ $CHECK_RET -eq 0 ]; then
    echo " All Tests Passed"
    exit 0
else
    echo " Tests Failed"
    exit 2
fi