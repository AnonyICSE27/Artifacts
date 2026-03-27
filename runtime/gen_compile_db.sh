exec 2>&1

# Source common functions
source "$(dirname "${BASH_SOURCE[0]}")/csa_utils.sh"

# ----------------------------
# Check input arguments
# ----------------------------
if [ $# -lt 1 ]; then
    echo "Usage: $0 <project_path>"
    exit 1
fi

PROJECT_PATH="$1"

# Set environment variables
set_env_vars

# Unpack environment if needed
unpack_environment

# Activate environment
activate_environment

# Verify CodeChecker
check_codechecker

# Generate compile_commands.json
generate_compile_db "$PROJECT_PATH"
exit $?
