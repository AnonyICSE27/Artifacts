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
    "build_log": "",
    "test_log": None,
    "compiled_num": -1,
}

if os.path.exists(f"{LOG_DIR}/test.log"):
    os.remove(f"{LOG_DIR}/test.log")

proc = subprocess.run(
    ["bash", "./test.sh"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    # timeout=3600,
)

build_log = read_file(f"{LOG_DIR}/build.log")
test_log = read_file(f"{LOG_DIR}/test.log")

result["build_log"] = build_log
result["test_log"] = test_log
result["compiled_num"] = proc.returncode
result["compiled_testlog"] = proc.stdout.decode()

if proc.returncode == 100:
    assert (
        "Failed to install libxml2-dev." in result["compiled_testlog"]
        or "Failed to install python-libxml2." in result["compiled_testlog"]
    )
    raise Exception("Failed to install dependencies. Check network.")

if proc.returncode != 1:
    result["compiled"] = True
    if proc.returncode == 0:
        result["passed"] = True
    else:
        result["passed"] = False
else:
    result["compiled"] = False
    result["passed"] = False


with open("test_result.json", "w") as f:
    json.dump(result, f, indent=2)
