import subprocess
import json
import os
import re

LOG_DIR = "/testcase/logs"

def read_file(path):
    if os.path.exists(path):
        with open(path, "r", errors="ignore") as f:
            return f.read()
    return ""

result = {
    "passed": False,
    "compiled": False,
    "real_failures": [],
    "build_log": "",
    "test_log": ""
}

# Clean old test logs
if os.path.exists(f"{LOG_DIR}/test.log"):
    os.remove(f"{LOG_DIR}/test.log")

if True:
    # Simulate Execute automation script
    proc = subprocess.run(
        ["bash", "./test.sh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # timeout=3600,
        text=True
    )

    build_content = read_file(f"{LOG_DIR}/build.log")
    test_log_content = read_file(f"{LOG_DIR}/test.log")

    result["build_log"] = build_content
    result["test_log"] = test_log_content

    # Determine status based on test.sh return code
    if proc.returncode == 0 or proc.returncode == 2:
        result["compiled"] = True
        
        # If return code is 0 and no failures, mark as passed
        if proc.returncode == 0:
            result["passed"] = True
    else:
        result["compiled"] = False

if False:
    result["build_log"] = f"Runtime Error: {str(e)}"

# Write result file
with open("test_result.json", "w") as f:
    json.dump(result, f, indent=2)