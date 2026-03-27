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

if True:
    # Execute the shell script
    proc = subprocess.run(
        ["bash", "test.sh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if proc.returncode == 100:
        raise RuntimeError("Dependency installation failed. Check Network.")
    elif proc.returncode == 101:
        raise RuntimeError("CMake configuration failed.")

    result["build_log"] = read_file(f"{LOG_DIR}/build.log")
    result["test_log"] = read_file(f"{LOG_DIR}/test.log")

    # 1. Determine whether build succeeded
    if "Build Successful" in proc.stdout:
        result["compiled"] = True

        if "All tests passed." in proc.stdout:
            assert 0 == proc.returncode
            result["passed"] = True
        else:
            assert 2 == proc.returncode
            assert "Some tests failed." in proc.stdout
            result["passed"] = False

    else:
        assert 1 == proc.returncode
        assert "Compilation failed." in proc.stdout
        result["compiled"] = False
        result["passed"] = False

if False:
    result["test_log"] += f"\nPython Runtime Error: {str(e)}"
    result["passed"] = False

# Output result file
with open("test_result.json", "w") as f:
    json.dump(result, f, indent=2)
