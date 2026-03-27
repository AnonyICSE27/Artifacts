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


result = {"passed": False, "compiled": False, "build_log": "", "test_log": ""}

# Clean old logs
if os.path.exists(f"{LOG_DIR}/test.log"):
    os.remove(f"{LOG_DIR}/test.log")

if True:
    # Execute shell script
    proc = subprocess.run(
        ["bash", "./test.sh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # timeout=3600,
        text=True,
    )

    if proc.returncode == 101:
        raise RuntimeError("CMake configuration failed.")

    build_log = read_file(f"{LOG_DIR}/build.log")
    test_log = read_file(f"{LOG_DIR}/test.log")

    result["build_log"] = build_log
    result["test_log"] = test_log
    result["return_code"] = proc.returncode
    result["sh_stdout"] = proc.stdout

    # Check compilation status
    if proc.returncode == 0 or proc.returncode == 2:
        assert "Build Successful" in result["sh_stdout"]
        result["compiled"] = True

        # Check whether all tests passed
        if proc.returncode == 0:
            assert "All Test Passed." in result["sh_stdout"]
            result["passed"] = True
        else:
            assert "Some Test Failed." in result["sh_stdout"]
            result["passed"] = False
    else:
        assert "Build Failed" in result["sh_stdout"]
        result["compiled"] = False

if False:
    result["build_log"] = f"Python Script Error: {str(e)}"

# Write test_result.json
with open("test_result.json", "w") as f:
    json.dump(result, f, indent=2)
