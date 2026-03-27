from os import getenv
from os.path import isfile
from dacite import from_dict
from dataclasses import dataclass, asdict
from typing import Any, Optional, List, Dict, Iterator
from ..basic import Patch, LLMQueryRecord
from ..model import Message, Model
from ..instance import BuildResult, ReproResult, Instance
from ..rs_utils import _abstractmethod, _load_json, _save_as_json
from ..utils import _dlog, _parse_search_replace_patch, SkipException
from .agent import Agent, AgentAskRecord


GeneratedPatch = Patch


@dataclass
class ValidationResult:
    patch: GeneratedPatch
    passed: bool  # build success and no sanitizer triggered
    fail_reason: Optional[str]  # ["build_fail", "sanitizer_triggered"]
    build_result: BuildResult
    repro_result: Optional[ReproResult]  # not None when build success


class PatchGenAgent(Agent):
    @dataclass
    class Input:
        instance: Instance

    @dataclass
    class Output:
        instance_info: Dict[str, Any]
        submission_patches: Dict[str, Optional[GeneratedPatch]]
        generated_patches: List[GeneratedPatch]
        validation_results: List[ValidationResult]
        extended_info: Dict[str, Any] = None
        llm_query_records: List[LLMQueryRecord] = None
        asked_agent_outputs: List[AgentAskRecord] = None

    def __init__(
        self,
        name: str,
        gen_all_then_val: bool = False,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.__gen_all_then_val = gen_all_then_val

    @_abstractmethod
    def _generate_patch(
        self,
        input: "Input",
        val_results: List[ValidationResult],
        return_dict: Dict[str, Any],
    ) -> Iterator[GeneratedPatch]:
        pass

    @_abstractmethod
    def _select_patch(
        self, val_results: List[ValidationResult]
    ) -> Dict[str, Optional[GeneratedPatch]]:
        pass

    def _ask_impl(self, input: "Input") -> "Output":
        @dataclass
        class _SavedGeneratedPatches:
            _L: List[GeneratedPatch]

        instance = input.instance
        load_patch_if_exist = getenv("LOAD_PATCH_IF_EXIST", "0") == "1"
        interrupt_after_patch_gen = getenv("INTERRUPT_AFTER_PATCH_GEN", "0") == "1"

        generated_patches: List[GeneratedPatch] = []
        validation_results: List[ValidationResult] = []
        addi_return_dict = {}

        generated_patches_jf = (
            f"{self._output_dir}/_T_generated_patches.json"
            if self._output_dir
            else None
        )
        addi_return_dict_jf = (
            f"{self._output_dir}/_T_addi_return_dict.json" if self._output_dir else None
        )
        # if True:  # Always generate patches NOW
        if not (
            load_patch_if_exist
            and generated_patches_jf
            and isfile(generated_patches_jf)
        ):
            self.dlog("No generated patches file, generating ...")
            patch_gen_iter = self._generate_patch(
                input=input,
                val_results=validation_results,
                return_dict=addi_return_dict,
            )
            if self.__gen_all_then_val:
                self.dlog("[GEN ALL] Generating patches ...")
                patch_gen_iter = list(patch_gen_iter)
                if addi_return_dict_jf:
                    _save_as_json(
                        addi_return_dict,
                        filename=addi_return_dict_jf,
                    )
                if generated_patches_jf:
                    _save_as_json(
                        asdict(_SavedGeneratedPatches(patch_gen_iter)),
                        filename=generated_patches_jf,
                    )
                    self.dlog(f"[GEN ALL] >>>> Saved {len(patch_gen_iter)} patches")
                self.dlog(f"[GEN ALL] Generated {len(patch_gen_iter)} patches")
                if interrupt_after_patch_gen:
                    raise SkipException("Interrupt after patch gen")
        else:
            self.dlog("Loading generated patches ...")
            patch_gen_iter = from_dict(
                data_class=_SavedGeneratedPatches,
                data=_load_json(generated_patches_jf),
            )._L
            assert isinstance(patch_gen_iter, list)
            self.dlog(f"Loaded {len(patch_gen_iter)} patches")
            if addi_return_dict_jf and isfile(addi_return_dict_jf):
                self.dlog("Loading addi_return_dict ...")
                addi_return_dict = _load_json(addi_return_dict_jf)
                self.dlog(f"Loaded addi_return_dict from {addi_return_dict_jf}")

        for patch_i, patch in enumerate(patch_gen_iter):
            generated_patches.append(patch)

            # Check & Parse
            sr_patches = _parse_search_replace_patch(
                patch.patch, remove_line_marker=True
            )
            # sr_patches = _parse_search_replace_patch(patch.patch)
            self.dlog(f"Test@{patch_i} -- Parsed a patch")
            self.dlog(f">>>> len(to-edit files): {len(sr_patches)}")
            for p in sr_patches:
                self.dlog(f">>>>>> {p.file}: {len(p.patches)} diffs")

            # Apply & Test
            self.ilog(f"Test@{patch_i} -- Testing a patch")

            o_file_contents = [
                # nullable when file does not exist
                instance.read_file(f"{instance.repo_path}/{sr_patch.file}")
                for sr_patch in sr_patches
            ]

            for i, (o_ctt, sr_patch) in enumerate(
                zip(o_file_contents, sr_patches, strict=True)
            ):
                if o_ctt is None:
                    try2_fn = f"{instance.repo_parent_dir}/{sr_patch.file}"
                    try2 = instance.read_file(try2_fn)
                    if try2 is not None:
                        repo_prefix = f"{instance.repo_name}/"
                        assert sr_patches[i].file.startswith(repo_prefix)
                        fixed_fn = sr_patch.file.removeprefix(repo_prefix)
                        self.dlog.w(f"Fixed file path: {sr_patch.file} -> {fixed_fn}")
                        o_file_contents[i] = try2
                        sr_patches[i].file = fixed_fn
                    del try2_fn, try2

            def _whitespace_insensitive_replace(text, old, new):
                import re

                parts = re.split(r"\s+", old.strip())
                pattern = r"\s+".join(re.escape(part) for part in parts)

                regex = re.compile(pattern)
                return regex.sub(new, text)

            p_file_contents = []
            for ctt, sr_patch in zip(o_file_contents, sr_patches, strict=True):
                if ctt is None:
                    # _dlog.w(f"To-edit file {sr_patch.file} does not exist")
                    p_file_contents.append(None)  # dummy value
                    continue
                patched_ctt = ctt
                for diff in sr_patch.patches:
                    patched_ctt = patched_ctt.replace(diff.search, diff.replace)
                if ctt == patched_ctt:
                    self.dlog.w("Bad patch; Try whitespace-insensitive replace")
                    for diff in sr_patch.patches:
                        patched_ctt = _whitespace_insensitive_replace(
                            patched_ctt, diff.search, diff.replace
                        )
                    if ctt == patched_ctt:
                        self.dlog.w("Still bad patch, keep going")
                p_file_contents.append(patched_ctt)
            assert len(sr_patches) == len(o_file_contents) == len(p_file_contents)
            for srp, o_ctt, p_ctt in zip(
                sr_patches,
                o_file_contents,
                p_file_contents,
                strict=True,
            ):
                if o_ctt is None:
                    assert p_ctt is None
                    self.dlog.w(f">>>> To-edit file {srp.file} does not exist")
                elif o_ctt.strip() == p_ctt.strip():
                    self.dlog.w(f">>>> Pacthed == original: {srp.file}")
                    for i, sr_patch in enumerate(srp.patches):
                        self.dlog.w(f">>>>>> diffs[{i}]: `{sr_patch.raw}`")
                else:
                    self.dlog(f">>>> Edited: {srp.file}")
            patch.file_paths = [sr_patch.file for sr_patch in sr_patches]
            patch.original_file_contents = o_file_contents
            patch.patched_file_contents = p_file_contents
            patch.index_in_patch_space = patch_i
            assert len(patch.patch) >= len(patch.file_paths)
            assert len(patch.file_paths) == len(patch.original_file_contents)
            assert len(patch.file_paths) == len(patch.patched_file_contents)
            build_r, repro_r = None, None
            with instance.apply_patch(patch):
                build_r = instance.build()  # secb build
                if build_r.success:
                    repro_r = instance.repro()  # secb repro
            assert build_r is not None
            assert not (build_r.success and repro_r is None)

            build_success: bool = build_r.success
            no_trigger_poc: bool = (
                repro_r is not None
                and not repro_r.timeout
                and not repro_r.sanitizer_triggered
            )
            if repro_r is not None and not repro_r.timeout and not no_trigger_poc:
                assert repro_r.sanitizer_triggered
            if repro_r is not None and not repro_r.timeout:
                assert repro_r.sanitizer_triggered == (not no_trigger_poc)

            validation_r = ValidationResult(
                patch=patch,
                passed=build_success and no_trigger_poc,
                fail_reason=(
                    "build_fail"
                    if not build_r.success
                    else (
                        (
                            "timeout_when_running_poc"
                            if repro_r.timeout
                            else "sanitizer_triggered"
                        )
                        if not no_trigger_poc
                        else None
                    )
                ),
                build_result=build_r,
                repro_result=repro_r,
            )
            self.ilog(f"Tested@{patch_i}")
            self.ilog(f">>>> passed: {validation_r.passed}")
            self.ilog(f">>>> fail_reason: {validation_r.fail_reason}")
            validation_results.append(validation_r)

        # 3. Select final patch by applying distinct strategies
        submission_patches = self._select_patch(validation_results)

        self.ilog(f"Finished patch generation")
        self.ilog(f">>>> len(gen patches): {len(generated_patches)}")
        for subm_i, (strategy, submp) in enumerate(submission_patches.items()):
            if submp:
                logging_patch = "\n\n".join(submp.patch)
                patch_select_details = submp.additional_info["patch_select_details"]
            else:
                logging_patch = None
                patch_select_details = None
            self.ilog(f">>>> [{subm_i}] strategy: {strategy}")
            self.ilog(f">>>>>> details: {patch_select_details}")
            self.ilog(f">>>>>> patch: `{logging_patch}`")

        return self.Output(
            instance_info=instance.config.to_dict(),
            submission_patches=submission_patches,
            generated_patches=generated_patches,
            validation_results=validation_results,
            extended_info=addi_return_dict.copy(),
        )
