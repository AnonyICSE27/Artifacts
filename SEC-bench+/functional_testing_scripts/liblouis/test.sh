#!/bin/bash
cd /src/liblouis

# 1. Environment preparation and cleanup
echo "🧹 Cleaning and bootstrapping..."
rm -f build.log test.log
# If no configure script, run autogen.sh (usually in cloned repositories)
if [ ! -f "./configure" ]; then
    ./autogen.sh > build.log 2>&1
fi

# 2. Configuration and Compilation
echo "🔨 Starting Build (UCS4 enabled)..."
# --enable-ucs4 supports 32-bit Unicode, better fitting modern system requirements
./configure --enable-ucs4 >> build.log 2>&1
make -j$(nproc) >> build.log 2>&1
MAKE_RET=$?

# Check compilation artifact lou_translate
if [ $MAKE_RET -eq 0 ] ; then
    echo " Build Successful"
else
    echo " Build Failed"
    exit 1
fi

# 3. Run official test suite (make check)
echo " Running Official Tests..."
# Note: If libyaml is installed in the environment, these tests will be very comprehensive
make check > test.log 2>&1
CHECK_RET=$?

if [ $CHECK_RET -eq 0 ]; then
    echo " All Tests Passed"
    exit 0
else
    echo " Tests Failed"
    exit 2
fi