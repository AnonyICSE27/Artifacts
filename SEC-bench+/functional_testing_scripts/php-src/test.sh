#!/bin/bash
cd /src/php-src

# 1. Environment Initialization
echo "🧹 Bootstrapping PHP build system..."
rm -f build.log test.log
if [ -f Makefile ]; then
    make clean >> build.log 2>&1
fi

grep "readdir_r(dirp, entry)" /src/php-src/main/reentrancy.c
RET=$?
if [ $RET -eq 0 ]; then
    echo " readdir_r(a, b) found, bad repo"
    git apply /testcase/repo_changes.diff
else
    echo " readdir_r(a, b) not found, it's ok"
fi

# Automatically generate configure
./buildconf --force >> build.log 2>&1

# Install dependencies
apt-get update && apt-get install -y libxml2-dev >> build.log 2>&1
RET=$?
if [ $RET -ne 0 ]; then
    echo " Dependency installation failed. Check Network."
    exit 100
fi

# 2. Configuration and Compilation
echo "🔨 Configuring and Building PHP..."
./configure \
    --disable-phpdbg \
    --enable-cli \
    --enable-debug \
    --with-libxml \
    --enable-zts \
    LIBS="-ldl" 
    >> build.log 2>&1

make -j$(nproc) >> build.log 2>&1
MAKE_RET=$?

# If compilation failed, exit directly, determined as not Compilation successful
if [ $MAKE_RET -ne 0 ]; then
    echo " PHP Build Failed"
    exit 1
fi
echo " PHP Build Successful"

# 3. Run tests
echo " Running PHP Test Suite..."
export NO_INTERACTION=1

# # Fix bad run-tests.php
# ## on docker desktop
# sed -i '/\$fp = fopen(\$file, "rb")/i \        if (empty($file)) return;' ./run-tests.php
# ## on ubuntu
# sed -i '/while\s*((\s*\$name\s*=\s*readdir(\s*\$o\s*)\s*)\s*!==\s*false\s*)\s*{/a \        if ($name == "") break;' ./run-tests.php

# Run test
make test TESTS=tests/basic -j$(nproc) >> test.log 2>&1
RET=$?
if [ $RET -ne 0 ]; then
    echo "Test Failed"
    exit 2
else
    echo "Test Passed"
    exit 0
fi
