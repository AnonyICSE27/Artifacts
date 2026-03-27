import subprocess
import json
import os
import re

LOG_DIR = "/src/jq"

def read_file(path):
    if os.path.exists(path):
        with open(path, "r", errors="ignore") as f:
            return f.read()
    return ""

result = {
    "passed": False,
    "compiled": False,
    "build_log": "",
    "test_log": "",
    "ignored_failures": ["tests/shtest"],
    "real_failures": []
}

if True:
    # Run test.sh
    proc = subprocess.run(
        ["bash", "test.sh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    if proc.returncode == 101:
        raise RuntimeError("Submodule update failed. Check Network.")


    # Read logs
    result["build_log"] = read_file(f"{LOG_DIR}/build.log")
    result["test_log"] = read_file(f"{LOG_DIR}/test.log")

    # Determine if compilation was successful
    if os.path.exists(f"{LOG_DIR}/jq"):
        result["compiled"] = True
    else:
        result["compiled"] = False
        result["passed"] = False
        raise RuntimeError("Build failed, jq binary not found")

    # Parse FAIL items
    fails = re.findall(r"^FAIL:\s+(tests/[^\s]+)", result["test_log"], re.MULTILINE)

    for f in fails:
        if f not in result["ignored_failures"]:
            result["real_failures"].append(f)

    # Final judgment
    if not result["real_failures"]:
        result["passed"] = True
    else:
        result["passed"] = False

if False:
    result["test_log"] += f"\nPython Error: {str(e)}"

# Output JSON
with open("test_result.json", "w") as f:
    json.dump(result, f, indent=2)
