#!/bin/bash
cd /src/yaml-cpp

# 1. Thoroughly clean and reconfigure
rm -rf build && mkdir build && cd build

# 2. Compilation Phase
echo "🔨 Building with CMake..."
cmake -DYAML_CPP_BUILD_TESTS=ON .. > build.log 2>&1
make -j$(nproc) >> build.log 2>&1
MAKE_RET=$?

# As long as make succeeds, consider compilation passed
if [ $MAKE_RET -eq 0 ]; then
    echo " Compilation successful"
else
    echo " Compilation failed"
    exit 1
fi

# 3. Run tests Phase
echo " Running unit tests..."
# Use CTest to run all bundled test items
ctest --output-on-failure > test.log 2>&1
TEST_RET=$?

if [ $TEST_RET -eq 0 ]; then
    echo " All tests passed"
    exit 0
else
    echo " Tests found errors"
    exit 2
fi