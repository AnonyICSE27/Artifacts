#!/bin/bash

LOG_DIR="/testcase/logs"
mkdir -p "$LOG_DIR"
BUILD_LOG="$LOG_DIR/build.log"
TEST_LOG="$LOG_DIR/test.log"

echo "[0/4] Installing dependencies..."
apt install python-libxml2 -y >> "$BUILD_LOG" 2>&1
RET=$?
if [ $RET -ne 0 ]; then
    echo "Failed to install python-libxml2."
    exit 100
fi

apt install libxml2-dev -y >> "$BUILD_LOG" 2>&1
RET=$?
if [ $RET -ne 0 ]; then
    echo "Failed to install libxml2-dev."
    exit 100
fi

if [ -f "/src/libredwg/test/xmlsuite/check.py" ]; then
    sed -i '1i#!/usr/bin/env python2' /src/libredwg/test/xmlsuite/check.py
fi

echo "[1/4] Cleaning old build artifacts..."
if [ -f Makefile ]; then
    # do NOT use distclean, it will delete some necessary files
    make clean >> "$BUILD_LOG" 2>&1 || true
fi
rm -rf autom4te.cache config.status

echo "[2/4] Building LibreDWG..."
# if [ ! -f "./configure" ]; then
sh ./autogen.sh >> "$BUILD_LOG" 2>&1
# fi

./configure --disable-shared >> "$BUILD_LOG" 2>&1

if make -j$(nproc) >> "$BUILD_LOG" 2>&1; then
    echo "Build Successful."
else
    echo "Build Failed!"
    exit 1
fi

echo "[3/4] Running tests..."
make check -j$(nproc) > "$TEST_LOG" 2>&1
if [ $? -eq 0 ]; then
    echo "Tests Completed Successfully."
    exit 0
else
    echo "Tests Encountered Errors."
    exit 2
fi
