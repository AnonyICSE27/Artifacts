#!/bin/bash

set_env_vars() {
    ENV_TAR="/static_analysis_tools/lsp_server.tar.gz"
    ENV_DIR="/static_analysis_tool_envs/lsp_server_env"
}

unpack_environment() {
    if [ ! -d "$ENV_DIR" ]; then
        echo "[INFO] Unpacking lsp_server environment to $ENV_DIR..."
        mkdir -p "$ENV_DIR"
        tar -xzf "$ENV_TAR" -C "$ENV_DIR"

        if [ -f "$ENV_DIR/bin/conda-unpack" ]; then
            echo "[INFO] Running conda-unpack..."
            "$ENV_DIR/bin/conda-unpack"
        fi
    else
        echo "[INFO] Using existing lsp_server environment."
    fi
}

activate_environment() {
    source "${ENV_DIR}/bin/activate"
}

check_environment() {
    bash -c 'pushd "/repo_toolkit" && python -c "import lsp_server" && popd'
    return $?
}

set_env_vars
unpack_environment
activate_environment
check_environment
exit $?
