#!/bin/bash

run_lsp_server() {
    local ENV_TAR="/static_analysis_tools/lsp_server.tar.gz"
    local ENV_DIR="/static_analysis_tool_envs/lsp_server_env"
    local TOOL_FILE="/repo_toolkit/lsp_server.py"
    
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
    
    source "${ENV_DIR}/bin/activate"
    echo "[INFO] Starting LSP server with args: $@"
    PYTHONDONTWRITEBYTECODE=1 python -u "$TOOL_FILE" "$@"
}

run_lsp_server "$@" > "/lsp_server.log" 2>&1 &
