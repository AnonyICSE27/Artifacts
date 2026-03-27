import os
import copy
import time
import argparse

from typing import AnyStr, Any, List, Dict
from dataclasses import asdict
from datasets import load_dataset
from . import rs_utils as rsu
from .utils import _dlog
from .model import Model
from .agents import SimpleVulRepairAgent
from .instance import InstanceConfig, make_instance_config


# fmt:off
def get_config() -> Dict[AnyStr, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_name", type=str, default=None)
    parser.add_argument("--model_configs_file", type=str, default=None)
    parser.add_argument("--embed_model_name", type=str, default=None)
    parser.add_argument("--embed_model_configs_file", type=str, default=None)
    parser.add_argument("--experiment", type=str, required=True)
    parser.add_argument("--dataset", type=str, required=True)
    parser.add_argument("--dataset_split", type=str, required=True)
    parser.add_argument("--instances", nargs="*", default=None)
    parser.add_argument("--output_dir", type=str, required=True)
    parser.add_argument("--cache_dir", type=str, required=True)
    parser.add_argument("--skip_if_error", action="store_true", default=False)
    parser.add_argument("--retry_skipped", action="store_true", default=False)
    parser.add_argument("--overwrite", action="store_true", default=False)

    # For `simple_vra`
    ## For SimplePatchGenAgent
    parser.add_argument("--simple_vra__context_window", type=int, default=None)
    parser.add_argument("--simple_vra__num_pacthes_to_gen", type=str, default=None)
    parser.add_argument("--simple_vra__patch_gen_strategy", type=str, default=None)
    ## For SimpleEditLocAgent
    parser.add_argument("--simple_vra__max_loc_files_with_prompting", type=int, default=None)
    parser.add_argument("--simple_vra__max_loc_files_with_retrieving", type=int, default=None)
    parser.add_argument("--simple_vra__chunk_size_when_retrieving", type=int, default=None)
    parser.add_argument("--simple_vra__chunk_overlap_when_retrieving", type=int, default=None)
    ## For all
    parser.add_argument("--simple_vra__enable_context_pre_collection", action="store_true", default=False)
    parser.add_argument("--simple_vra__enable_safety_property_analysis", action="store_true", default=False)

    args = parser.parse_args()

    if not os.path.isfile(args.model_configs_file):
        raise ValueError(f"Model config file {args.model_configs_file} not exists")
    if not os.path.isfile(args.embed_model_configs_file):
        raise ValueError(
            f"Model config file {args.embed_model_configs_file} not exists"
        )

    args.model_configs = rsu._load_json(args.model_configs_file)
    del args.model_configs_file
    if args.model_name not in args.model_configs:
        raise ValueError(f"Model {args.model_name} not in {args.model_configs.keys()}")

    args.embed_model_configs = rsu._load_json(args.embed_model_configs_file)
    del args.embed_model_configs_file
    if args.embed_model_name not in args.embed_model_configs:
        raise ValueError(
            f"Model {args.embed_model_name} not in {args.embed_model_configs.keys()}"
        )

    if args.instances == ["all"]:
        args.instances = None

    if args.overwrite:
        rsu._wlog(f"Type 'overwrite' to remove {args.output_dir}: ", end="")
        if input().strip() != "overwrite":
            raise ValueError("Not confirmed to overwrite")
        rsu._wlog(f"Removing {args.output_dir}...")
        rsu._sp_system(f"rm -rf {args.output_dir}", logging=False)

    return vars(args)
# fmt:on


def _run_simple_vul_repair_agent(
    instance_config: InstanceConfig,
    config: Dict[AnyStr, Any],
    instance_output_dir: str,
    instance_cache_dir: str,
) -> None:
    assert os.path.isdir(instance_output_dir)
    arg_prefix = f'{config["experiment"]}__'
    agent = SimpleVulRepairAgent(
        model=Model.make(**config["model_configs"][config["model_name"]]),
        embed_model_config=config["embed_model_configs"][config["embed_model_name"]],
        **{
            k.removeprefix(arg_prefix): v
            for k, v in config.items()
            if k.startswith(arg_prefix)
        },
        output_dir=instance_output_dir,
        cache_dir=instance_cache_dir,
    )
    agent_output = agent.ask(agent.Input(instance_config=instance_config))
    agent.save_output(agent_output)


def repair_instances(instances: List[Dict[AnyStr, Any]], config: Dict[AnyStr, Any]):
    _agent_run_fn = {
        "simple_vra": _run_simple_vul_repair_agent,
    }[config["experiment"]]

    instance_keys = config["instances"]
    rsu._ilog(f"Loaded ALL: {len(instances)} instances")
    if not instance_keys:
        rsu._ilog(f"Testing ALL: {len(instances)} instances")
    elif ":" in instance_keys[0]:
        assert len(instance_keys) == 1
        start_idx, end_idx = instance_keys[0].split(":")
        instances = eval(f"ins[{start_idx}:{end_idx}]", {"ins": instances})
        rsu._ilog(f"Testing l(ALL[{start_idx}:{end_idx}]): {len(instances)} instances")
    else:
        instances = [i for i in instances if any(k in i["id"] for k in instance_keys)]
        rsu._ilog(f"Testing specified: {len(instances)} instances (by {instance_keys})")

    output_dir = config["output_dir"]
    cache_dir = config["cache_dir"]
    for i, instance in enumerate(instances, start=1):
        instance = instance.copy()
        instance_id = instance.pop("instance_id")
        i_start_time = time.time()
        i_output_dir = os.path.join(output_dir, instance_id)
        i_log_file = os.path.join(i_output_dir, "log_{suffix}.ansi")
        i_finish_mark_jf = os.path.join(i_output_dir, "__finished__.json")
        i_skip_mark_jf = os.path.join(i_output_dir, "__skip__.json")
        i_lock_file = os.path.join(output_dir, f"{instance_id}.lock")
        i_cache_dir = os.path.join(cache_dir, instance_id)
        rsu._ilog(f">>==== Repairing {instance_id} ({i}/{len(instances)})... ====<<")

        for i in range(10000):
            try_log_file = i_log_file.format(suffix=i + 1)
            if not os.path.exists(try_log_file):
                i_log_file = try_log_file
                break
        else:
            assert False, "too many log files"

        if config["retry_skipped"] and os.path.isfile(i_skip_mark_jf):
            rsu._wlog(f"Specify `retry_skipped`, renaming skip file ...")
            for i in range(1, 10000):
                new_skip_f = f"{i_output_dir}/__skip__.{i}.json"
                if not os.path.isfile(new_skip_f):
                    os.rename(i_skip_mark_jf, new_skip_f)
                    break
            else:
                assert False, f"Too many skip files in {i_output_dir}"
            assert not os.path.isfile(i_skip_mark_jf)

        if os.path.isfile(i_finish_mark_jf):
            rsu._wlog(f"Already tested, skipped: {instance_id}")
            continue

        if os.path.isfile(i_skip_mark_jf):
            rsu._wlog(f"Already ensured skipped, skipped: {instance_id}")
            continue

        # TODO: Use bug_report or bug_description ?????
        ## bug_description (https://github.com/SEC-bench/aider/blob/9fab04216c566ef39acbb56a60639006cbb4d03c/benchmark/sec_bench/infer.py#L371
        ## bug_report (https://github.com/SEC-bench/OpenHands/blob/627427fccbf6ea213efdb115ed1e248588dca9c1/evaluation/benchmarks/sec_bench/run_infer.py#L210
        ## bug_report (https://github.com/SEC-bench/SWE-agent/blob/ee0e2d0fdd8dead4eae73cb6ad77e47faab2523f/sweagent/run/batch_instances.py#L275
        # NOTE: Use bug_report now

        def _run_agent():
            _dlog(f"Instance details")
            _dlog(f">>>> id: {instance_id}")
            _dlog(f">>>> repo: {instance.get('repo', 'N/A')}")
            _dlog(f">>>> project: {instance.get('project_name', instance.get('project', 'N/A'))}")
            _dlog(f">>>> lang: {instance.get('lang', 'N/A')}")
            _dlog(f">>>> working dir: {instance.get('work_dir', 'N/A')}")

            try:
                _dlog(f">>>> bug report: `{instance['bug_report']}`")
            except:
                pass
            try:
                _dlog(f">>>> bug report: `{instance['issue_report'] or instance['sanitizer_report']}`")
            except:
                pass

            instance_config = make_instance_config(instance)

            try:
                _agent_run_fn(instance_config, config, i_output_dir, i_cache_dir)
                i_end_time = time.time()
                i_addi_dict = {}
                i_addi_dict["start_time"] = i_start_time
                i_addi_dict["end_time"] = i_end_time
                i_addi_dict["duration"] = i_end_time - i_start_time
                rsu._save_as_json(i_addi_dict, i_finish_mark_jf)
            except Exception as ex:
                if not config["skip_if_error"]:
                    raise ex

                import traceback, io

                tb = io.StringIO()
                traceback.print_exc(file=tb)
                tb = tb.getvalue()

                rsu._wlog(f"Meet a exception: {ex}")
                rsu._wlog(f">>>> logging file: {i_log_file}")
                rsu._wlog(f">>>> skip instance: {instance_id}")
                rsu._wlog(f">>>> traceback: `{tb}`")
                rsu._save_as_json(
                    {
                        "instance_id": instance_id,
                        "reason": str(ex),
                        "traceback": tb,
                        "log_file": i_log_file,
                    },
                    filename=i_skip_mark_jf,
                )

        with rsu.FileLock(i_lock_file, retry=0) as flock:  # Allow multiple processes
            if flock.locked:
                assert not os.path.exists(i_log_file)
                rsu._ilog(f"Running {config['experiment']} for {instance_id}")
                rsu._ilog(f">>>> log file: {i_log_file}")
                os.makedirs(i_output_dir, exist_ok=True)
                with rsu._set_log_to_file(i_log_file, "w"):
                    _start_time_to_show = time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(i_start_time),
                    )
                    rsu._ilog(f"SSSS============================================SSSS")
                    rsu._ilog(f">>>> Start Time: {_start_time_to_show}")
                    rsu._ilog(f"----------------------------------------------------")

                    _run_agent()

                    rsu._ilog(f"EEEE============================================EEEE")
                    del _start_time_to_show
            else:
                rsu._wlog(f"Another process is running for this instance")
                rsu._wlog(f">>>> skip instance: {instance_id}")


def main(config: Dict[AnyStr, Any]) -> int:
    def _mask_config(config):
        """Mask config for logging.
        1. Mask all `api_key`s
        """

        config = copy.deepcopy(config)
        model_configs = config["model_configs"]
        embed_model_configs = config["embed_model_configs"]
        for _, v in model_configs.items():
            if "api_key" in v.keys():
                v["api_key"] = "***"
        for _, v in embed_model_configs.items():
            if "api_key" in v.keys():
                v["api_key"] = "***"
        return config

    rsu._ilog("=============== [Agent4AVR] ===============")
    rsu._plog("Config", _mask_config(config))

    os.environ["INIT_CHECK_COMPILABLE"] = "1"
    config["_envs"] = os.environ.copy()

    rsu._ilog(f"Loading dataset {config['dataset']}['{config['dataset_split']}']")

    if os.path.isfile(config["dataset"]) and config["dataset"].endswith(".json"):
        if config["dataset_split"] != "all":
            raise ValueError(f"Only support `all` split for json dataset")
        dataset = rsu._load_json(config["dataset"])
        if not isinstance(dataset, list):
            raise ValueError(f"Dataset {config['dataset']} is not a list of instances.")
    else:
        dataset = load_dataset(config["dataset"], split=config["dataset_split"])
    instances = [{"id": item["instance_id"], **dict(item)} for item in dataset]
    rsu._ilog(f"Loaded {len(instances)} instances from {config['dataset']}")

    # Make output dir
    os.makedirs(config["output_dir"], exist_ok=True)
    # Save config as a json file
    config_file = os.path.join(config["output_dir"], "__config.json")
    rsu._save_as_json(_mask_config(config), filename=config_file)

    # Start repairing projects
    repair_instances(instances, config=config)
    rsu._ilog("=============== [   END   ] ===============")
