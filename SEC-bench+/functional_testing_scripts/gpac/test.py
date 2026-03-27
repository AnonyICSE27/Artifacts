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


result = {"passed": False, "compiled": False, "build_log": None, "test_log": None}

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
result["sh_ret"] = proc.returncode
result["sh_output"] = proc.stdout.decode()

if proc.returncode == 100:
    assert "SYNC_MEDIA_FAILED" in result["sh_output"]
    raise Exception("Failed to sync media. Check network.")
if proc.returncode == 101:
    assert "NO_MEDIA_PACKAGE_FOUND" in result["sh_output"]
    raise Exception("No media found. Bad state.")
if proc.returncode == 102:
    assert "INSTALL_TIME_FAILED" in result["sh_output"]
    raise Exception("Failed to install time. Check network.")

if "BUILD_SUCCESS" in proc.stdout.decode():
    result["compiled"] = True
    assert proc.returncode == 0
    # Tests passed 149 (78 %)
    re_match = re.search(r"Tests passed (\d+) \((\d+) %\)", test_log)
    passing_rate = int(re_match.group(2) if re_match else "0")
    result["passing_percent"] = passing_rate
    print(f"passing_percent: {passing_rate}%")
    if result["passing_percent"] >= 75:
        result["passed"] = True
    else:
        result["passed"] = False
else:
    assert "BUILD_FAILED" in result["sh_output"]
    assert proc.returncode == 1
    result["compiled"] = False
    result["passed"] = False

with open("test_result.json", "w") as f:
    json.dump(result, f, indent=2)
