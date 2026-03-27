import subprocess
import json
import os

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
    # timeout=3600
)

build_log = read_file(f"{LOG_DIR}/build.log")
test_log = read_file(f"{LOG_DIR}/test.log")

result["build_log"] = build_log
result["compiled_num"] = proc.returncode
result["compiled_testlog"] = proc.stdout.decode()

if proc.returncode == 100:
    assert (
        "BAD_STATE: Build success, but binary not found." in result["compiled_testlog"]
    )
    raise Exception("BAD_STATE: Build success, but binary not found.")


if proc.returncode == 0 or proc.returncode == 2:
    assert "BUILD_SUCCESS" in result["compiled_testlog"]
    result["compiled"] = True

    if proc.returncode == 0:
        assert "TEST_SUCCESS" in result["compiled_testlog"]
        result["passed"] = True
        result["test_log"] = test_log
    else:
        assert proc.returncode == 2
        assert "TEST_FAILED" in result["compiled_testlog"]
        result["passed"] = False
        result["test_log"] = test_log

else:
    assert proc.returncode == 1
    assert "BUILD_FAILED" in result["compiled_testlog"]
    result["compiled"] = False
    result["passed"] = False


with open("test_result.json", "w") as f:
    json.dump(result, f, indent=2)
