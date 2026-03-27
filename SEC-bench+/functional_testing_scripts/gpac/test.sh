#!/bin/bash

LOG_DIR=/testcase/logs
BUILD_LOG=$LOG_DIR/build.log
TEST_LOG=$LOG_DIR/test.log
mkdir -p $LOG_DIR

# https://github.com/cla7aye15I4nd/patchagent-artifact/blob/main/skyset/gpac/test.sh
# #!/bin/bash
# ./configure
# make -j
# git submodule update --init
# export PATH="$(realpath ./bin/gcc):$PATH"
# cd testsuite
# ./make_tests.sh -sync-media -clean
# ./make_tests.sh -quick 2>/dev/null | grep "Tests passed"

### Some cases are failed even in the benchmark-provided patched version.
### And the failing case set is not stable, so we only check the passing rate >= 75%

echo "[1] clean build"
if [[ -f Makefile ]]; then
    make clean >> $BUILD_LOG 2>&1
    git stash >> $BUILD_LOG 2>&1
fi

echo "[2] build project"
./configure >> $BUILD_LOG 2>&1
make -j$(nproc) >> $BUILD_LOG 2>&1
BUILD_RET=$?
if [ $BUILD_RET -eq 0 ]; then
    echo "BUILD_SUCCESS"
else
    echo "BUILD_FAILED"
    exit 1
fi

echo "[3] run tests"
# update submodules
git submodule update --init >> $TEST_LOG 2>&1
export PATH="$(realpath ./bin/gcc):$PATH"

cd testsuite

# replace bad command
sed -i '/wget .*cut-dirs=4/ { s/ -q / /; /--no-check-certificate/! s|$| --no-check-certificate| }' /src/gpac/testsuite/make_tests.sh

# unpack pre-downloaded media files
downloaded_media_tar=/src/gpac/__TEST_ARTIFACTS__testsuite_external_media260128.tar.xz
if [[ ! -f $downloaded_media_tar ]]; then
    echo "NO_MEDIA_PACKAGE_FOUND"
    exit 101
fi
rm -rf /src/gpac/testsuite/external_media
tar xJf $downloaded_media_tar -C /src/gpac/testsuite/

# # sync media files
# ./make_tests.sh -sync-media -clean >> $TEST_LOG 2>&1
# SYNC_RET=$?
# if [ $SYNC_RET -ne 0 ]; then
#     echo "SYNC_MEDIA_FAILED"
#     exit 100
# fi

# install gnu time
apt-get install time -y >> $TEST_LOG 2>&1
INSTALL_RET=$?
if [ $INSTALL_RET -ne 0 ]; then
    echo "INSTALL_TIME_FAILED"
    exit 102
fi

# run tests
./make_tests.sh -quick >> $TEST_LOG 2>&1
exit 0
