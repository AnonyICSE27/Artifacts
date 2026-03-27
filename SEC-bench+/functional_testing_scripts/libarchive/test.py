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
    "fail_log": [],
}

if os.path.exists(f"{LOG_DIR}/test.log"):
    os.remove(f"{LOG_DIR}/test.log")

proc = subprocess.run(
    ["bash", "./test.sh"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    # timeout=3600,
    text=True,
)

build_log = read_file(f"{LOG_DIR}/build.log")
test_log = read_file(f"{LOG_DIR}/test.log")

result["build_log"] = build_log
result["test_log"] = test_log
result["compiled_testlog"] = proc.stdout

if "BUILD_SUCCESS" in proc.stdout:
    result["compiled"] = True

if result["compiled"] and test_log:
    # all_fail_lines = [
    #     line.strip() for line in test_log.split("\n") if "FAIL:" in line
    # ]
    # filtered_fails = [line for line in all_fail_lines if "alive.test" not in line]
    # result["fail_log"] = filtered_fails

    # if len(filtered_fails) == 0:
    #     if "Segmentation fault" not in test_log:
    #         result["passed"] = True
    #     else:
    #         result["fail_log"].append(
    #             "CRITICAL: Segmentation fault detected in logs"
    #         )

    if "TEST_SUCCESS" in proc.stdout:
        result["passed"] = True
    elif "TEST_FAILED" in proc.stdout:
        result["passed"] = False
    else:
        raise Exception("Test log format error.")

with open("test_result.json", "w") as f:
    json.dump(result, f, indent=2)
