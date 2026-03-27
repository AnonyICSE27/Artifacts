#!/bin/bash
# Script: Compile libheif and run tests
cd /src/libheif

LOG_DIR="/testcase/logs"
mkdir -p "$LOG_DIR"
BUILD_LOG="$LOG_DIR/build.log"
TEST_LOG="$LOG_DIR/test.log"

# 1. Environment initialization
echo "Cleaning up previous build artifacts..."
if [ -f Makefile ]; then
    make clean >> "$BUILD_LOG" 2>&1
fi
rm -rf build.log test.log

# 2. Install dependencies (libheif core dependencies)
echo "Installing dependencies..."
apt-get update && apt-get install -y \
    cmake make pkg-config g++ \
    libde265-dev libx265-dev libjpeg-dev >> "$BUILD_LOG" 2>&1
RET=$?
if [ $RET -ne 0 ]; then
    echo " Dependency installation failed. Check Network."
    exit 100
fi

# 3. Configuration and Compilation
echo "Configuring with 'testing' preset..."
# Use testing preset to enable unit tests
cmake --preset=testing . >> "$BUILD_LOG" 2>&1
BUILD_CONF_RET=$?

if [ $BUILD_CONF_RET -ne 0 ]; then
    echo " CMake configuration failed."
    exit 101
fi

echo "Building project..."
make -j$(nproc) >> "$BUILD_LOG" 2>&1
MAKE_RET=$?

if [ $MAKE_RET -ne 0 ]; then
    echo "Compilation failed."
    exit 1
fi
echo "Build Successful."

# 4. Run tests
echo " Running CTest..."
# Some tests are failed even in the benchmark-provided patched version, we ignore them.
ctest --output-on-failure --exclude-regex "encode|region" > "$TEST_LOG" 2>&1
RET=$?

if [ $RET -eq 0 ]; then
    echo "All tests passed."
    exit 0
else
    echo "Some tests failed."
    exit 2
fi
