import os
import sys
import tqdm
import json
import glob
import argparse
from collections import defaultdict
from datasets import load_dataset

sys.path.append(os.path.abspath(f"{os.path.dirname(__file__)}/.."))
from Agent4AVR.utils import _get_git_diff


parser = argparse.ArgumentParser()
parser.add_argument("-d", "--dir", type=str, required=True)
parser.add_argument("-o", "--output_dir", type=str, required=True)
parser.add_argument("-s", "--slice", type=str, default="0:80")
args = parser.parse_args()

path = os.path.abspath(args.dir)
output_dir = os.path.abspath(args.output_dir)
slice_ = args.slice
generate_preds_file = True
assert os.path.isdir(path)

print("Loading dataset")
dataset = load_dataset("SEC-bench/SEC-bench", split="eval")
instances = {
    item["instance_id"]: {"id": item["instance_id"], **dict(item)} for item in dataset
}

if ":" in slice_:
    a, b = [e.strip() for e in slice_.split(":")]
    a = int(a or "0")
    b = int(b or len(instances))
    instance_ids = [item["instance_id"] for item in dataset][a:b]
    del a, b, slice_
else:
    instance_ids = [i for i in slice_.split(",") if i in instances]
print(f"Instaces to be collected: {len(instance_ids)}")

repo2iids = defaultdict(list)
for instance_id in instance_ids:
    repo2iids[instances[instance_id]["project_name"]].append(instance_id)
repo2iids = dict(repo2iids)
print(f"Total repos: {len(repo2iids)}")

output_files = glob.glob(f"{path}/**/output.json", recursive=True)
output_files = [e for e in output_files if e.split("/")[-2] in instance_ids]
print(f"Tested: {len(output_files)}")

s2preds = {}
for output_file in tqdm.tqdm(output_files):
    instance_id = output_file.split("/")[-2]
    if instance_id not in instance_ids:
        continue

    with open(output_file, "r") as f:
        result = json.load(f)

    ppoc_index_in_patch_space = None
    for s, p in result["asked_agent_outputs"][1]["agent_output"][
        "submission_patches"
    ].items():
        if s == "mj_voting_on_pocpassing":
            if s not in s2preds:
                s2preds[s] = []
            s2preds[s].append(
                {
                    "instance_id": instance_id,
                    "model_name_or_path": os.path.basename(path),
                    "model_patch": (
                        _get_git_diff(
                            file_paths=p["file_paths"],
                            old_contents=p["original_file_contents"],
                            new_contents=p["patched_file_contents"],
                        )
                        if p
                        else None
                    ),
                }
            )
            break


print("Generating preds files ...")
s, preds = list(s2preds.items())[0]
assert set(s2preds.keys()) == {"mj_voting_on_pocpassing"}
assert s == "mj_voting_on_pocpassing"
no_submitted_ins = set(instance_ids) - {pred["instance_id"] for pred in preds}
for nos_iid in no_submitted_ins:
    preds.append(
        {
            "instance_id": nos_iid,
            "model_name_or_path": os.path.basename(path),
            "model_patch": None,
        }
    )
assert len(preds) == len(instance_ids)

s_preds_file = f"{output_dir}/preds.json"
os.makedirs(output_dir, exist_ok=True)
preds_j = {pred["instance_id"]: pred for pred in preds}
with open(s_preds_file, "w") as fp:
    json.dump(preds_j, fp, indent=2)
print(f">>>> {s}: {s_preds_file} ({len(preds_j)})")
