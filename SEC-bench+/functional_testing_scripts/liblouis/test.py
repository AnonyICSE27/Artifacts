import subprocess
import json
import os

LOG_DIR = "/src/liblouis"

def read_file(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(path, "r") as f:
        return f.read()

result = {
    "passed": False,
    "compiled": False,
    "real_failures": [],
    "build_log": "",
    "test_log": ""
}

if True:
    proc = subprocess.run(
        ["bash", "test.sh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    result["build_log"] = read_file(f"{LOG_DIR}/build.log")
    result["test_log"] = read_file(f"{LOG_DIR}/test.log")

    if proc.returncode == 0:
        result["compiled"] = True
        result["passed"] = True
    elif proc.returncode == 2:
        result["compiled"] = True
        result["passed"] = False
        result["real_failures"] = ["liblouis_official_check_failed"]
    else:
        result["compiled"] = False
        result["passed"] = False

if False:
    result["test_log"] += f"\nPython Error: {str(e)}"

with open("test_result.json", "w") as f:
    json.dump(result, f, indent=2)
