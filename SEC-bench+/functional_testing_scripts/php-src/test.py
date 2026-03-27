import subprocess
import json
import os
import re

LOG_DIR = "/src/php-src"

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
    # Run shell script
    proc = subprocess.run(
        ["bash", "test.sh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    if proc.returncode == 100:
        raise RuntimeError("Dependency installation failed. Check Network.")

    result["build_log"] = read_file(f"{LOG_DIR}/build.log")
    result["test_log"] = read_file(f"{LOG_DIR}/test.log")
    result["sh_ret"] = proc.returncode
    result["sh_output"] = proc.stdout

    if "PHP Build Successful" in result["sh_output"]:
        assert result["sh_ret"] != 1
        result["compiled"] = True
        if "Test Passed" in result["sh_output"]:
            assert 0 == result["sh_ret"]
            result["passed"] = True
        else:
            assert 2 == result["sh_ret"]
            assert "Test Failed" in result["sh_output"]
            result["passed"] = False
    else:
        assert result["sh_ret"] == 1
        assert "PHP Build Failed" in result["sh_output"]
        result["compiled"] = False
        result["passed"] = False

if False:
    result["test_log"] += f"\nPython Runtime Error: {str(e)}"
    result["passed"] = False

# Write final JSON
with open("test_result.json", "w") as f:
    json.dump(result, f, indent=2)
