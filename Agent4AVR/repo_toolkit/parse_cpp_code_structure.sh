#!/bin/bash

set_env_vars() {
    ENV_TAR="/static_analysis_tools/py-libclang-18.1.1.tar.gz"
    ENV_DIR="/static_analysis_tool_envs/py-libclang-18.1.1"
    TOOL_FILE="/repo_toolkit/parse_cpp_code_structure.py"
}

unpack_environment() {
    if [ ! -d "$ENV_DIR" ]; then
        echo "[INFO] Unpacking py-libclang-18.1.1 environment to $ENV_DIR..."
        mkdir -p "$ENV_DIR"
        tar -xzf "$ENV_TAR" -C "$ENV_DIR"

        if [ -f "$ENV_DIR/bin/conda-unpack" ]; then
            echo "[INFO] Running conda-unpack..."
            "$ENV_DIR/bin/conda-unpack"
        fi
    else
        echo "[INFO] Using existing py-libclang-18.1.1 environment."
    fi
}

activate_environment() {
    source "${ENV_DIR}/bin/activate"
}

check_libclang() {
    local check_output=$(python -m pip show libclang)
    if [ $? -ne 0 ]; then
        echo "[ERROR] libclang is not installed in the environment."
        return 1
    else
        echo "[INFO] libclang is installed in the environment."
        printf "%s" "$check_output"
        return 0
    fi
}

{
    set_env_vars
    unpack_environment
    activate_environment
    check_libclang
} 1>/dev/null 2>/dev/null

python /repo_toolkit/parse_cpp_code_structure.py $@ 2>/dev/null
exit $?
