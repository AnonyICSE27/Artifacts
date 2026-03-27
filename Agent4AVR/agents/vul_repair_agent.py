import os
from dacite import from_dict
from dataclasses import dataclass, asdict
from typing import Any, Optional, List, Dict
from ..rs_utils import _load_json, _save_as_json
from ..basic import LLMQueryRecord
from ..model import Message, Model
from ..instance import InstanceConfig, Instance, make_instance
from .agent import Agent, AgentAskRecord, _record_agent_ask
from .simple_edit_loc_agent import SimpleEditLocAgent
from .simple_patch_gen_agent import SimplePatchGenAgent


class SimpleVulRepairAgent(Agent):
    @dataclass
    class Input:
        instance_config: InstanceConfig

    @dataclass
    class Output:
        instance_info: Dict[str, Any]
        llm_query_records: List[LLMQueryRecord] = None
        asked_agent_outputs: List[AgentAskRecord] = None

    def __init__(
        self,
        model: Model,
        # For SimpleEditLocAgent
        embed_model_config: Dict[str, Any],
        max_loc_files_with_prompting: int,
        max_loc_files_with_retrieving: int,
        chunk_size_when_retrieving: int,
        chunk_overlap_when_retrieving: int,
        # For SimplePatchGenAgent
        context_window: int,
        num_pacthes_to_gen: str,
        patch_gen_strategy: str,
        # For w/ context pre-collection
        enable_context_pre_collection: bool = False,
        # For w/ safety property generation
        enable_safety_property_analysis: bool = False,
        **kwargs,
    ):
        super().__init__("SimpleVulRepairAgent", **kwargs)

        self.__model = model
        self.__loc_agent = SimpleEditLocAgent(
            model=self.__model,
            embed_model_config=embed_model_config,
            max_loc_files_with_prompting=max_loc_files_with_prompting,
            max_loc_files_with_retrieving=max_loc_files_with_retrieving,
            chunk_size_when_retrieving=chunk_size_when_retrieving,
            chunk_overlap_when_retrieving=chunk_overlap_when_retrieving,
            enable_context_pre_collection=enable_context_pre_collection,
            enable_safety_property_analysis=enable_safety_property_analysis,
            **kwargs,
        )
        self.__patch_gen_agent = SimplePatchGenAgent(
            model=self.__model,
            context_window=context_window,
            num_pacthes_to_gen=num_pacthes_to_gen,
            strategy=patch_gen_strategy,
            gen_all_then_val=os.getenv("GEN_ALL_THEN_VAL", "0") == "1",
            **kwargs,
        )

    def _ask_impl(self, input: "Input") -> "Output":
        instance_config = input.instance_config
        self.ilog(f"Repairing instance: {instance_config.id}")
        self.ilog(f">>>> repo: {instance_config.repo}")
        self.ilog(f">>>> project: {instance_config.project_name}")
        self.ilog(f">>>> base commit: {instance_config.base_commit}")

        self.ilog(f"Starting remote instance ...")
        with make_instance(instance_config) as instance:
            self.ilog(f"Started remote instance: {instance.id}")

            if os.getenv("INIT_CHECK_COMPILABLE", "0") == "1":
                self.ilog(f"O. Try to compile the instance ...")
                build_r = instance.build()  # check whether the project can be compiled
                if not build_r.success:
                    self.wlog(f"Failed to compile instance: {instance.config.id}")
                    raise ValueError(
                        f"Failed to compile instance: {instance.config.id}"
                        + f">>>> success: {build_r.success}"
                        + f">>>> timeout: {build_r.timeout}"
                        + f">>>> exit code: {build_r.exit_code}"
                        + f">>>> raw output: `{build_r.raw_output}`"
                    )
                self.ilog(f"Success to compile instance: {instance.config.id}")

            self.ilog(f"I. Locating vulnerability locations ...")
            la_output_file = f"{self._output_dir}/_loc_output.json"
            if not os.path.isfile(la_output_file):
                la_output = self.__loc_agent.ask(self.__loc_agent.Input(instance))
                _save_as_json(asdict(la_output), la_output_file)
                self.dlog.i(f"Saved vul loc output to {la_output_file}")
            else:
                la_output = from_dict(
                    data_class=self.__loc_agent.Output,
                    data=_load_json(la_output_file),
                )
                _record_agent_ask(
                    self.__loc_agent.name,
                    self.__loc_agent.itendifier,
                    agent_output=la_output,
                )
                self.dlog.i(f"Loaded vul loc output from {la_output_file}")

            self.ilog(f"II. Generating patches ...")
            pga_output = self.__patch_gen_agent.ask(
                self.__patch_gen_agent.Input(
                    instance=instance,
                    issue_descriptions=la_output.issue_descriptions,
                    suspicious_file_infos=la_output.suspicious_file_infos,
                    suspicious_elements=la_output.suspicious_elements,
                )
            )

            return self.Output(
                instance_info=instance.config.to_dict(),
            )

    def _save_output(self, output: "Output", output_dir: str):
        import os, json

        assert os.path.isdir(output_dir)
        with open(f"{output_dir}/output.json", "w") as fp:
            json.dump(asdict(output), fp, indent=2)
