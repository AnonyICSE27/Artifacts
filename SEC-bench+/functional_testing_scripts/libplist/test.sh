#!/bin/bash
cd /src/libplist

# 1. Environment Cleanup
echo "🧹 Cleaning environment..."
[ -f Makefile ] && make distclean > /dev/null 2>&1
rm -f build.log test.log

# 2. Build Phase
echo "🔨 Starting Build..."
./autogen.sh --without-cython > build.log 2>&1  # Exclude python bindings to speed up and reduce dependencies
make -j$(nproc) >> build.log 2>&1
MAKE_RET=$?

# Check artifacts: usually plistutil is generated in tools directory
PLIST_TOOL="./tools/plistutil"
if [ $MAKE_RET -eq 0 ] && [ -f "$PLIST_TOOL" ]; then
    echo " Build Successful"
else
    echo " Build Failed"
    exit 1
fi

# 3. Automated functional testing
echo " Running Smoke Test..."
# Try running tool to check version or help info
$PLIST_TOOL --help > test.log 2>&1
SMOKE_RET=$?

# Run bundled test suite
make check >> test.log 2>&1
CHECK_RET=$?

if [ $SMOKE_RET -eq 0 ] && [ $CHECK_RET -eq 0 ]; then
    echo " All Tests Passed"
    exit 0
else
    echo " Tests Failed"
    exit 2
fi