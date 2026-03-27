#!/bin/bash

# Common functions for clang static analyzer scripts

# ----------------------------
# Set environment variables
# ----------------------------
set_env_vars() {
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    ENV_TAR="$SCRIPT_DIR/clang_static_analyzer.tar.gz"
    ENV_DIR="/static_analysis_tool_envs/clang_static_analyzer_env"
    CLANG18_TAR="$SCRIPT_DIR/clang+llvm-18.1.8-x86_64-linux-gnu-ubuntu-18.04.tar.xz"
    CLANG18_DIR="/static_analysis_tool_envs/clang-18"
}

# ----------------------------
# Setup LLVM environment
# ----------------------------
setup_llvm_environment() {
    if [ ! -d "$CLANG18_DIR" ]; then
        echo "[INFO] Unpacking clang+llvm-18.1.8 to $CLANG18_DIR..."
        mkdir -p "$CLANG18_DIR"
        tar -xf "$CLANG18_TAR" -C "$CLANG18_DIR" --strip-components=1
        
        # # Set environment variables
        # export LLVM_HOME="$CLANG18_DIR"
        # export PATH="$LLVM_HOME/bin:$PATH"
        # export LD_LIBRARY_PATH="$LLVM_HOME/lib:$LD_LIBRARY_PATH"

        # Create symlink for clang-extdef-mapping
        if [ ! -f "/usr/local/bin/clang-extdef-mapping" ]; then
            echo "[INFO] Creating symlink for clang-extdef-mapping..."
            ln -s "$CLANG18_DIR/bin/clang-extdef-mapping" /usr/local/bin/clang-extdef-mapping
        fi

        # Create symlink for libtinfo.so.5
        if [ -f "$SCRIPT_DIR/libtinfo.so.5.9" ]; then
            echo "[INFO] Creating symlink for libtinfo.so.5..."
            ln -sf "$SCRIPT_DIR/libtinfo.so.5.9" "/lib/x86_64-linux-gnu/libtinfo.so.5"
        fi
    # else
    #     echo "[INFO] Using existing clang-18 environment."
    #     export LLVM_HOME="$CLANG18_DIR"
    #     export PATH="$LLVM_HOME/bin:$PATH"
    #     export LD_LIBRARY_PATH="$LLVM_HOME/lib:$LD_LIBRARY_PATH"
    fi
}

# ----------------------------
# Unpack environment if needed
# ----------------------------
unpack_environment() {
    setup_llvm_environment
    
    if [ ! -d "$ENV_DIR" ]; then
        echo "[INFO] Unpacking clang_static_analyzer environment to $ENV_DIR..."
        mkdir -p "$ENV_DIR"
        tar -xzf "$ENV_TAR" -C "$ENV_DIR"

        if [ -f "$ENV_DIR/bin/conda-unpack" ]; then
            echo "[INFO] Running conda-unpack..."
            "$ENV_DIR/bin/conda-unpack"
        fi
    else
        echo "[INFO] Using existing clang_static_analyzer environment."
    fi
}

# ----------------------------
# Activate environment
# ----------------------------
activate_environment() {
    source "${ENV_DIR}/bin/activate"
}

# ----------------------------
# Verify CodeChecker availability
# ----------------------------
check_codechecker() {
    if ! command -v CodeChecker >/dev/null 2>&1; then
        echo "[ERROR] CodeChecker is not found in the environment."
        exit 2
    else
        echo "$(CodeChecker version)"
    fi
}

# ----------------------------
# Generate compile_commands.json
# ----------------------------
generate_compile_db() {
    local project_path="$1"
    local compile_db="$project_path/compile_commands.json"
    
    if [ -f "$compile_db" ]; then
        echo "[INFO] compile_commands.json already exists at:"
        echo "       $compile_db"
        echo "[INFO] Skipping generation."
        return 0
    fi

    echo "[INFO] Generating compile_commands.json..."
    echo "[INFO] Command: CodeChecker log -b \"cd $project_path && AGENT4AVR_DISABLE_SANITIZER=1 secb build\" -o \"$compile_db\""

    CodeChecker log -b "cd $project_path && AGENT4AVR_DISABLE_SANITIZER=1 secb build" -o "$compile_db"

    if [ -f "$compile_db" ]; then
        echo "[INFO] Successfully generated: $compile_db"
        return 0
    else
        echo "[ERROR] Failed to generate compile_commands.json"
        return 3
    fi
}
