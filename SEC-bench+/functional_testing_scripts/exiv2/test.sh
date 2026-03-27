#!/bin/bash

LOG_DIR="/testcase/logs"
mkdir -p "$LOG_DIR"

BUILD_LOG="$LOG_DIR/build.log"
TEST_LOG="$LOG_DIR/test.log"

echo "[START]" > "$BUILD_LOG"
echo "[START]" > "$TEST_LOG"

cd /src/exiv2 || exit 1

chmod +x /src/exiv2/test/tiff-test.sh
mkdir -p /src/exiv2/test/tmp

grep "extern int strerror_r(" /src/exiv2/src/futils.cpp
RET=$?
if [ $RET -eq 0 ]; then
    echo "int strerror_r(int, char*, size_t) found, bad repo"
    git apply /testcase/repo_changes.diff
else
    echo "int strerror_r(int, char*, size_t) not found, it's ok"
fi

# ---------- Build ----------
rm -rf build
mkdir build
cd build || exit 1

cmake .. -G "Unix Makefiles" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_STANDARD=11 \
    -DEXIV2_BUILD_UNIT_TESTS=OFF \
    -DEXIV2_BUILD_SAMPLES=ON >> "$BUILD_LOG" 2>&1

if make -j$(nproc) >> "$BUILD_LOG" 2>&1; then
  echo "BUILD_SUCCESS" >> "$BUILD_LOG"
else
  echo "BUILD_FAILED" >> "$BUILD_LOG"
  exit 0
fi

# ---------- Tests ----------
make tests -j$(nproc) >> "$TEST_LOG" 2>&1
RET=$?

if [ $RET -eq 0 ]; then
  echo "TEST_SUCCESS"
else
  echo "TEST_FAILED"
fi
