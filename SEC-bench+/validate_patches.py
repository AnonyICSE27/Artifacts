import os
import sys
import csv
import json
import time
import shutil
import argparse
import subprocess
from dataclasses import asdict
from datasets import load_dataset

# Level 0: APPLY_PATCH
# Level 1: +BUILD
# Level 2: ++POC (#Poc)
# Level 3: +++EXIT_CODE (#Exec)
# Level 4: ++++FUNC_TEST (#Func)

THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# os.environ["DEBUG"] = "1"
os.environ["STATIC_ANALYSIS_TOOLS_DIR"] = f"{THIS_DIR}/../runtime"

sys.path.append(f"{THIS_DIR}/..")

from Agent4AVR.instance import (
    BuildResult,
    ReproResult,
    TestResult,
    make_instance_config,
    make_instance,
)


parser = argparse.ArgumentParser()
parser.add_argument("--slice", type=str, default=":80")
parser.add_argument("--ignore", nargs="+", default=None)
parser.add_argument("--include", nargs="+", default=None)
parser.add_argument("--preds", type=str, required=True)
parser.add_argument("--resume", action="store_true", default=False)
parser.add_argument("--workers", type=int, default=1)
parser.add_argument("--verbose", action="store_true", default=False)

args = parser.parse_args()
preds_file = os.path.abspath(args.preds)
slice_ = args.slice
ignore_keys = args.ignore
include_keys = args.include
resume = args.resume
num_workers = args.workers
verbose = args.verbose

if os.getenv("NUM_WORKERS") is not None:
    print("Overriding num_workers from environment variable NUM_WORKERS")
    num_workers = int(os.getenv("NUM_WORKERS"))
    os.environ.pop("NUM_WORKERS")  # avoid recursive call

if not os.path.isfile(preds_file):
    raise ValueError(f"Preds file {preds_file} does not exist.")
with open(preds_file, "r") as fp:
    preds = json.load(fp)
basebase_path = os.path.splitext(preds_file)[0]
output_json_file = basebase_path + "_validation_results.json"
wip_output_jsonl_file = basebase_path + "_validation_results.cache.jsonl"
output_csv_file = basebase_path + "_validation_results.csv"

_print_fn = print


def print(msg: str = None, **kwargs):
    if msg is None:
        _print_fn("", **kwargs)
        return
    _print_fn(f"\033[94m[SEC-bench+] {msg}\033[0m", **kwargs)


def print_verbose(msg: str = None, **kwargs):
    if verbose:
        print(msg, **kwargs)


AUTO_OVERWRITE = os.getenv("AUTO_OVERWRITE", "0") == "1"
SKIP_IF_EXISTS = os.getenv("SKIP_IF_EXISTS", "0") == "1"
if AUTO_OVERWRITE:
    if os.path.exists(output_json_file):
        for i in range(100):
            new_output_json_file = f"{output_json_file}.bak{i}"
            if not os.path.exists(new_output_json_file):
                break
        else:
            raise ValueError(f"Cannot find a free backup file name.")
        os.rename(output_json_file, new_output_json_file)
        print_verbose(f"Backup original file to {new_output_json_file}")
    if os.path.exists(output_csv_file):
        for i in range(100):
            new_output_csv_file = f"{output_csv_file}.bak{i}"
            if not os.path.exists(new_output_csv_file):
                break
        else:
            raise ValueError(f"Cannot find a free backup file name.")
        os.rename(output_csv_file, new_output_csv_file)
        print_verbose(f"Backup original file to {new_output_csv_file}")
if SKIP_IF_EXISTS:
    if os.path.isfile(output_json_file) and os.path.isfile(output_csv_file):
        print(f"Output files {output_json_file} and {output_csv_file} already exist.")
        sys.exit(0)

if os.path.exists(output_json_file):
    if os.path.isfile(output_json_file):
        if (
            input(f"Output file {output_json_file} already exists. Overwrite? (y/n) ")
            == "y"
        ):
            os.remove(output_json_file)
        else:
            raise ValueError(f"Output file {output_json_file} already exists.")
    else:
        raise ValueError(f"Output file {output_json_file} is a directory.")
if os.path.exists(output_csv_file):
    if os.path.isfile(output_csv_file):
        if (
            input(f"Output file {output_csv_file} already exists. Overwrite? (y/n) ")
            == "y"
        ):
            os.remove(output_csv_file)
        else:
            raise ValueError(f"Output file {output_csv_file} already exists.")
    else:
        raise ValueError(f"Output file {output_csv_file} is a directory.")

# -----------------------------------------------------------------------------------

if num_workers > 1:
    print(f"Spawning {num_workers} workers ...")
    this_cli = os.path.abspath(__file__)
    this_py = sys.executable
    working_dir = basebase_path + "_multiproc_working_dir"

    os.makedirs(working_dir, exist_ok=True)
    preds_parts = [{} for _ in range(num_workers)]
    for i, (instance_id, pred_d) in enumerate(preds.items()):
        preds_parts[i % num_workers][instance_id] = pred_d

    preds_part_files = []
    worker_log_files = []
    cache_files = []
    for i, pred_part in enumerate(preds_parts):
        with open(os.path.join(working_dir, f"preds_part{i}.json"), "w") as fp:
            json.dump(pred_part, fp, indent=2)
        pred_part_base = os.path.join(working_dir, f"preds_part{i}")
        preds_part_files.append(pred_part_base + ".json")
        worker_log_files.append(os.path.join(working_dir, f"worker_{i}.log"))
        cache_files.append(pred_part_base + "_validation_results.cache.jsonl")
    for i, cache_file in enumerate(cache_files):
        if os.path.isfile(wip_output_jsonl_file) and not os.path.isfile(cache_file):
            shutil.copyfile(wip_output_jsonl_file, cache_file)

    commands = []
    for i in range(num_workers):
        commands.append(
            [
                "SKIP_IF_EXISTS=1",
                this_py,
                "-u",
                this_cli,
                "--slice",
                slice_,
                *(["--ignore", *ignore_keys] if ignore_keys else []),
                *(["--include", *include_keys] if include_keys else []),
                "--preds",
                os.path.join(working_dir, f"preds_part{i}.json"),
                "--resume",
                "--workers",
                "1",
                *(["--verbose"] if verbose else []),
                f">> {worker_log_files[i]} 2>&1",
            ]
        )

    def run_command(worker_id: int, cmd: list, log_file: str):
        cmd = " ".join(cmd)
        print(f"[{worker_id}] Running command: {cmd}")
        print(f">>> See log: tail -f {log_file}")
        # non-blocking call
        proc = subprocess.Popen(cmd, shell=True)
        return proc

    def see_progress(worker_id: int, cache_file: str):
        if not os.path.isfile(cache_file):
            print(f"[{worker_id}] Cache file {cache_file} does not exist.")
            return set()
        with open(cache_file, "r") as fp:
            iids = list(json.loads(L.strip())["instance_id"] for L in fp)
        print(f"[{worker_id}] Processed {len(iids)} instances so far.")
        return set(iids)

    # launch all commands
    sub_procs = [
        run_command(
            worker_id=i,
            cmd=cmd,
            log_file=worker_log_files[i],
        )
        for i, cmd in enumerate(commands)
    ]
    # wait for all commands to finish & watch their progress
    while True:
        print()
        print("----------------------------------------")
        print()
        finished_iids = set()
        for i, cache_file in enumerate(cache_files):
            finished_iids |= see_progress(worker_id=i, cache_file=cache_file)
        print()
        print(f"Total finished instances so far: {len(finished_iids)}")
        rets = [proc.poll() for proc in sub_procs]
        for i, ret in enumerate(rets):
            if ret is not None and ret != 0:
                print(f"Error: Worker {i} exited with code {ret}.")
        if None in rets:
            pass  # some workers still running
        elif set(rets) == set([0]):
            break  # all workers finished
        else:
            raise ValueError(f"All workers exited, but some with errors: {rets}")
        print()
        print(f"Working  IDs: {[i for i, r in enumerate(rets) if r is None]}")
        print(f"Finished IDs: {[i for i, r in enumerate(rets) if r == 0]}")
        print(f"Errored  IDs: {[i for i, r in enumerate(rets) if r not in {None, 0}]}")
        print("----------------------------------------")
        time.sleep(10)  # check every 10 seconds
    for proc in sub_procs:
        proc.wait()
    print("All workers finished.")

    # collect results & merge them
    # wip_output_jsonl_file = basebase_path + "_validation_results.cache.jsonl"
    done_in_main = set()
    if os.path.exists(wip_output_jsonl_file):
        with open(wip_output_jsonl_file, "r") as fp:
            for j_line in fp:
                j = json.loads(j_line.strip())
                iid = j["instance_id"]
                done_in_main.add(iid)
    iid2result = {}
    for cache_file in cache_files:
        with open(cache_file, "r") as fp:
            for j_line in fp:
                j = json.loads(j_line.strip())
                iid = j["instance_id"]
                if iid in done_in_main:
                    continue
                iid2result.setdefault(iid, []).append(j)
    with open(wip_output_jsonl_file, "a") as fp:  # NOTE: "a"
        for iid, results in iid2result.items():
            count_submitted = sum(1 for r in results if r["submitted"])
            assert count_submitted <= 1  # at most one submission per instance
            if count_submitted == 1:
                final_result = next(r for r in results if r["submitted"])
                fp.write(json.dumps(final_result) + "\n")
            else:
                assert count_submitted == 0
                dummy_result = {
                    "instance_id": instance_id,
                    "submitted": False,
                    "patch_apply_success": False,
                    "build_success": False,
                    "poc_test_success": False,
                    "exit_code_check_success": False,
                    "func_test_success": False,
                    "submitted_patch": None,
                    "build_result": None,
                    "poc_test_result": None,
                    "exit_code_check_result": None,
                    "func_test_result": None,
                }
                fp.write(json.dumps(dummy_result) + "\n")

    # re-run with this cache file
    cmd = [
        this_py,
        "-u",
        this_cli,
        "--slice",
        slice_,
        *(["--ignore", *ignore_keys] if ignore_keys else []),
        *(["--include", *include_keys] if include_keys else []),
        "--preds",
        preds_file,
        "--resume",
        "--workers",
        "1",
        *(["--verbose"] if verbose else []),
    ]
    cmd = " ".join(cmd)
    print(f"Collecting final results from {wip_output_jsonl_file} ...")
    subprocess.run(cmd, shell=True, check=True)

    exit(0)

# -----------------------------------------------------------------------------------

wip_validation_results = {}
if not resume:
    if os.path.exists(wip_output_jsonl_file):
        for i in range(100):
            new_wip_output_jsonl_file = f"{wip_output_jsonl_file}.bak{i}"
            if not os.path.exists(new_wip_output_jsonl_file):
                break
        else:
            raise ValueError(f"Cannot find a free backup file name.")
        os.rename(wip_output_jsonl_file, new_wip_output_jsonl_file)
    assert not os.path.exists(wip_output_jsonl_file)
elif os.path.exists(wip_output_jsonl_file):
    print(f"Loading previous validation results from {wip_output_jsonl_file} ...")
    with open(wip_output_jsonl_file, "r") as fp:
        for j_line in fp:
            j = json.loads(j_line.strip())
            wip_validation_results[j["instance_id"]] = j
            print_verbose(f">>>> Loaded previous result for {j['instance_id']}")
            del j_line, j

print("Loading dataset SEC-bench/SEC-bench")
# NOTE: do NOT use: dataset = load_dataset("SEC-bench/SEC-bench", split="eval")
# NOTE: use our local version, the SEC-bench[eval]'s HF version is updated, I don't like the uncontrolled changes
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
dataset_json = f"{THIS_DIR}/secbench_details.json"
with open(dataset_json, "r") as fp:
    dataset = json.load(fp)
instances = [{"id": item["instance_id"], **dict(item)} for item in dataset]
try:
    print_verbose(f"Filtering instances with slice {slice_} ...")
    instances = eval(f"_ins[{slice_}]", {"_ins": instances})
except Exception as e:
    raise ValueError(f"Invalid slice {slice_}: {e}")
if ignore_keys is not None:
    print_verbose(f"Filtering out instances with id containing {ignore_keys} ...")
    instances = [i for i in instances if not any(k in i["id"] for k in ignore_keys)]
if include_keys is not None:
    print_verbose(f"Finding instances with id containing {include_keys} ...")
    instances = [i for i in instances if any(k in i["id"] for k in include_keys)]
print(f">>>> To be validated: {len(instances)}")
print()


validation_results = []
for i, instance_d in enumerate(instances, start=1):
    print(f"======== Validating [{i}/{len(instances)}]: {instance_d["id"]} ========")
    instance_d = instance_d.copy()
    instance_id = instance_d.pop("instance_id")
    instance_config = make_instance_config(instance_d)

    if instance_id in wip_validation_results:
        print(f"Instance {instance_id} has been validated.")
        cached_result = wip_validation_results[instance_id].copy()
        if "patch_apply_success" not in cached_result:
            # keep backward compatibility
            cached_result["patch_apply_success"] = True
        validation_results.append(cached_result)
        continue

    submitted_patch = preds.get(instance_id, {}).get("model_patch", None)
    if submitted_patch is None or submitted_patch.strip() == "":
        print(f"Warning: No patch submitted for instance {instance_id}.")
        validation_results.append(
            {
                "instance_id": instance_id,
                "submitted": False,
                "patch_apply_success": False,
                "build_success": False,
                "poc_test_success": False,
                "exit_code_check_success": False,
                "func_test_success": False,
                "submitted_patch": submitted_patch,
                "build_result": None,
                "poc_test_result": None,
                "exit_code_check_result": None,
                "func_test_result": None,
            }
        )
        continue

    submitted = True
    patch_apply_success = False

    build_success = False  # Level 1
    poc_test_success = False  # Level 2
    exit_code_check_success = False  # Level 3
    func_test_success = False  # Level 4

    build_result = None  # Level 1
    poc_test_result = None  # Level 2
    exit_code_check_result = None  # Level 3
    func_test_result = None  # Level 4

    start_timestamp = time.time()
    applied_patch_timestamp = None
    built_timestamp = None
    poc_tested_timestamp = None
    tested_timestamp = None
    end_timestamp = None

    print_verbose("Starting remote instance ...")
    with make_instance(instance_config) as instance:
        started_remote_timestamp = time.time()
        print_verbose(f"Started remote instance: {instance.id}")

        print_verbose(f"Applying submitted patch ...")
        base_commit = instance.get_head_commit_hash()
        if submitted_patch.strip() != "":
            try:
                submitted_patch = submitted_patch.replace("\r\n", "\n")
                submitted_patch = submitted_patch + "\n"
                new_commit = instance.apply_git_diff(
                    submitted_patch,
                    message="Apply submitted patch",
                )
                assert new_commit != base_commit
                patch_apply_success = True
                applied_patch_timestamp = time.time()
                print_verbose(f">>>> {base_commit} -> {new_commit}")
            except Exception as ex:
                assert any(
                    msg in str(ex)
                    for msg in [
                        "Failed to apply git diff",
                        "Failed to commit applied git diff",
                    ]
                )
                patch_apply_success = False
                print_verbose(f"Failed to apply submitted patch: {ex}")
        else:
            # the empty patch can be used to check whether the patch validation works
            print_verbose(f"Empty patch applied.")
            patch_apply_success = True
            applied_patch_timestamp = time.time()

        if patch_apply_success:
            print_verbose(f"Level 1. Check whether the patched project compiles ...")
            build_r: BuildResult = instance.build()
            build_result = asdict(build_r)
            build_success = not build_r.timeout and build_r.success
            built_timestamp = time.time()
            if not build_success:
                print_verbose(f"Failed to compile patch: {instance.config.id}")
            if build_r.timeout:
                raise ValueError(f"Build timed out for instance {instance.config.id}")

        if build_success:
            print_verbose(f"Level 2. Check whether the POC test passes ...")
            repro_r: ReproResult = instance.repro()
            poc_test_success = not repro_r.timeout and not repro_r.sanitizer_triggered
            poc_test_result = asdict(repro_r)
            poc_tested_timestamp = time.time()
            if not poc_test_success:
                print_verbose(f"Failed to pass POC test: {instance.config.id}")

        if poc_test_success:
            print_verbose(f"Level 3. Check whether the exit code is as expected ...")
            exit_code_check_success = repro_r._exit_code in [0, instance_d["exit_code"]]
            exit_code_check_result = {
                "exit_code": repro_r._exit_code,
                "expected_exit_code": [0, instance_d["exit_code"]],
            }
            if not exit_code_check_success:
                print_verbose(f"Failed to pass exit code check: {instance.config.id}")

        if exit_code_check_success:
            print_verbose(f"Level 4. Check whether the functional test passes ...")
            test_r: TestResult = instance.test()
            func_test_success = not test_r.timeout and test_r.passed
            func_test_result = asdict(test_r)
            tested_timestamp = time.time()
            if not func_test_success:
                print_verbose(f"Failed to pass functional test: {instance.config.id}")

        end_timestamp = time.time()

    assert start_timestamp is not None
    assert started_remote_timestamp is not None
    # assert applied_patch_timestamp is not None
    # assert built_timestamp is not None
    # assert poc_tested_timestamp is not None
    # assert tested_timestamp is not None
    assert end_timestamp is not None

    validation_result = {
        "instance_id": instance_id,
        "submitted": submitted,
        "patch_apply_success": patch_apply_success,
        "build_success": build_success,
        "poc_test_success": poc_test_success,
        "exit_code_check_success": exit_code_check_success,
        "func_test_success": func_test_success,
        "submitted_patch": submitted_patch,
        "build_result": build_result,
        "poc_test_result": poc_test_result,
        "exit_code_check_result": exit_code_check_result,
        "func_test_result": func_test_result,
        "timestamps": {
            "start": start_timestamp,
            "started_remote": started_remote_timestamp,
            "applied_patch": applied_patch_timestamp,  # nullable
            "built": built_timestamp,  # nullable
            "poc_tested": poc_tested_timestamp,  # nullable
            "tested": tested_timestamp,  # nullable
            "end": end_timestamp,
            "total_duration": end_timestamp - start_timestamp,
        },
    }
    validation_results.append(validation_result)
    with open(wip_output_jsonl_file, "a") as fp:
        fp.write(json.dumps(validation_result) + "\n")

    print(f"Finished validating patch for instance {instance_id}.")
    print(f">>>> Level 0 (PATCH_APPLY): {patch_apply_success}")
    print(f">>>> Level 1 (+BUILD): {build_success}")
    print(f">>>> Level 2 (++POC): {poc_test_success}")
    print(f">>>> Level 3 (+++EXIT_CODE): {exit_code_check_success}")
    print(f">>>> Level 4 (+++FUNC_TEST): {func_test_success}")
    print()


patch_apply_success_instances = []  # Level 0
build_success_instances = []  # Level 1
poc_test_success_instances = []  # Level 2
exit_code_check_success_instances = []  # Level 3
func_test_success_instances = []  # Level 4
for result in validation_results:
    if result["patch_apply_success"]:
        patch_apply_success_instances.append(result["instance_id"])
    if result["build_success"]:
        build_success_instances.append(result["instance_id"])
    if result["poc_test_success"]:
        poc_test_success_instances.append(result["instance_id"])
    if result["exit_code_check_success"]:
        exit_code_check_success_instances.append(result["instance_id"])
    if result["func_test_success"]:
        func_test_success_instances.append(result["instance_id"])

# Save validation results
assert not os.path.exists(output_json_file)
assert not os.path.exists(output_csv_file)

with open(output_json_file, "w") as fp:
    json.dump(validation_results, fp, indent=2)

with open(output_csv_file, "w") as fp:
    csv_writer = csv.writer(fp)
    csv_writer.writerow(
        [
            "Instance",
            "Submitted",
            "PATCH_APPLY",
            "PATCH_APPLY+Build",
            "PATCH_APPLY+Build+POC",
            "PATCH_APPLY+Build+POC+EXIT_CODE",
            "PATCH_APPLY+Build+POC+EXIT_CODE+FUNC_TEST",
        ]
    )
    for result in validation_results:
        csv_writer.writerow(
            [
                result["instance_id"],
                result["submitted"],
                result["patch_apply_success"],
                result["build_success"],
                result["poc_test_success"],
                result["exit_code_check_success"],
                result["func_test_success"],
            ]
        )
    csv_writer.writerow(
        [
            "Total",
            sum([result["submitted"] for result in validation_results]),
            len(patch_apply_success_instances),
            len(build_success_instances),
            len(poc_test_success_instances),
            len(exit_code_check_success_instances),
            len(func_test_success_instances),
        ]
    )

print()
print(f"#validated: {len(validation_results)}")
print(f"#submitted: {sum([result['submitted'] for result in validation_results])}")
print(f"#patch_apply success: {len(patch_apply_success_instances)}")
print(f"#+build success: {len(build_success_instances)}")
print(f"#++POC success: {len(poc_test_success_instances)}")
print(f"#+++EXIT_CODE success: {len(exit_code_check_success_instances)}")
print(f"#++++FUNC_TEST success: {len(func_test_success_instances)}")

instances_w_subm_patch = [
    result["instance_id"] for result in validation_results if result["submitted"]
]
print_verbose("Instances with the submitted patch:")
print_verbose(f">>>> {instances_w_subm_patch}")

print_verbose("Instances with the patch apply success:")
print_verbose(f">>>> {patch_apply_success_instances}")

print_verbose("Instances with the build success:")
print_verbose(f">>>> {build_success_instances}")

print_verbose("Instances with the build+POC success:")
print_verbose(f">>>> {poc_test_success_instances}")

print_verbose("Instances with the build+POC+EXIT_CODE success:")
print_verbose(f">>>> {exit_code_check_success_instances}")

print_verbose("Instances with the build+POC+EXIT_CODE+FUNC_TEST success:")
print_verbose(f">>>> {func_test_success_instances}")
