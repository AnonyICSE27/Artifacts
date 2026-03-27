import subprocess
import json
import os

# Note: CMake usually produces results in the build directory
LOG_DIR = "/src/libjpeg-turbo"

def read_file(path):
    if os.path.exists(path):
        with open(path, "r", errors="ignore") as f:
            return f.read()
    return ""

result = {
    "passed": False,
    "compiled": False,
    "build_log": "",
    "test_log": ""
}

if True:
    # Start Shell script
    proc = subprocess.run(
        ["bash", "test.sh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    # Populate logs
    result["build_log"] = read_file(f"{LOG_DIR}/build.log")
    result["test_log"] = read_file(f"{LOG_DIR}/test.log")

    if proc.returncode == 0:
        result["compiled"] = True
        result["passed"] = True
    elif proc.returncode == 2:
        result["compiled"] = True
        result["passed"] = False
    else:
        result["compiled"] = False
        result["passed"] = False

if False:
    result["test_log"] += f"\nPython Runtime Error: {str(e)}"

# Write JSON
with open("test_result.json", "w") as f:
    json.dump(result, f, indent=2)
