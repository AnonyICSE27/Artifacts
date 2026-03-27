import subprocess
import json
import os

LOG_DIR = "/testcase/logs"
BUILD_LOG = f"{LOG_DIR}/build.log"
TEST_LOG = f"{LOG_DIR}/test.log"


def read_file(path):
    if os.path.exists(path):
        with open(path, "r", errors="ignore") as f:
            return f.read()
    return ""


subprocess.run(
    [
        "find",
        "/src/exiv2/tests",
        "-name",
        "*.py",
        "-exec",
        "sed",
        "-i",
        "s/assertEquals/assertEqual/g",
        "{}",
        "+",
    ]
)

proc = subprocess.run(
    ["bash", "./test.sh"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)

build_log = read_file(BUILD_LOG)
test_log = read_file(TEST_LOG)

result = {
    "compiled": False,
    "passed": False,
    "build_log": build_log,
    "test_log": test_log,
    "sh_ret": proc.returncode,
    "sh_output": proc.stdout.decode(),
}

assert 0 == result["sh_ret"]  # always return 0

if "BUILD_SUCCESS" in build_log:
    result["compiled"] = True
else:
    assert "BUILD_FAILED" in build_log
    result["compiled"] = False
    result["passed"] = False

if not result["compiled"]:
    result["passed"] = False
else:
    if "TEST_SUCCESS" in result["sh_output"]:
        result["passed"] = True
    elif "TEST_FAILED" in result["sh_output"]:
        result["passed"] = False
    else:
        raise Exception("Unknown test result.")

with open("test_result.json", "w") as f:
    json.dump(result, f, indent=2)
