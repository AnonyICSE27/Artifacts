from io import StringIO
from os import getenv
from typing import Any, Optional, Union, List, Dict, Iterator
from dataclasses import dataclass
from ..basic import LLMQueryRecord
from ..model import Message, Model
from ..instance import Instance
from ..utils import (
    _parse_multi_code_blocks,
    _format_cpp_code,
    _collapse_ellipsis,
    _detect_PL_from_file_suffix,
    _parse_git_diff,
)
from .agent import Agent, AgentAskRecord
from .patch_gen_agent import GeneratedPatch, ValidationResult, PatchGenAgent
from .prompts import (
    DEFAULT_SYSTEM_PROMPT,
    SIMPLE_PATCH_GENERATION_PROMPT,
)


class SimplePatchGenAgent(PatchGenAgent):
    @dataclass
    class Input:
        instance: Instance
        issue_descriptions: Dict[str, Any]
        suspicious_file_infos: Dict[str, Any]
        suspicious_elements: List[Dict[str, Any]]

    def __init__(
        self,
        model: Model,
        context_window: int,
        num_pacthes_to_gen: str,
        strategy: Optional[str] = None,
        **kwargs,
    ):
        super().__init__("SimplePGA", **kwargs)
        strategy = strategy or "_simple_generate_patch"
        # note: set context_window to 10, refer to agentless
        self.__model = model
        self.__context_window = context_window
        self.__num_pacthes_to_gen = num_pacthes_to_gen
        self._generate_patch = getattr(self, strategy)
        self.dlog(f"Selected pacth gen strategy: {strategy}")

    def _construct_context(
        self,
        suspicious_file_infos: Dict[str, Any],
        suspicious_elements: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        # Return: [
        ##      {
        ##          "file": ...,
        ##          "content": ...,
        ##          "original_content": ...,
        ##      }, ...
        ## ]

        abspath2element = {}

        def _build(elts: list | None):
            for elt in elts or []:
                if elt["abs_path"] in abspath2element:
                    # no-body element should not cover with-body element
                    if elt["body_range"] is None:
                        continue
                abspath2element[elt["abs_path"]] = elt
                if "children" in elt:
                    _build(elt["children"])

        for _, info in suspicious_file_infos.items():
            _build(info["elements"])
        del _build

        file2elts = {f: [] for f in suspicious_file_infos.keys()}
        for elt in suspicious_elements:
            abs_path = elt["abs_path"]
            if abs_path in abspath2element:  # ignoring unrecognized element
                file2elts[elt["file"]].append(abspath2element[abs_path])
            else:
                self.dlog.w(f"Unrecognized element: {abs_path} ({elt['type']})")
        total_valid_sus_elements = sum([len(elts) for elts in file2elts.values()])

        if total_valid_sus_elements == 0:
            self.dlog.w("No valid sus. element, use no-full-match strategy")
            file2elts = {f: [] for f in suspicious_file_infos.keys()}
            for elt in suspicious_elements:
                abs_path = elt["abs_path"]
                elt_name = abs_path.split("::")[-1].strip()
                elt_match_cnt = 0
                for t_path, t_elt in abspath2element.items():
                    t_filep = t_path.split("::")[0].strip()
                    t_name = t_path.split("::")[-1].strip()
                    if t_name == elt_name:
                        file2elts[t_filep].append(t_elt)
                        elt_match_cnt += 1
                        # do not break, may match multiple elts, it's ok
                if elt_match_cnt == 0:
                    self.dlog.w(f"Unrecognized element: {abs_path} ({elt['type']})")
            total_valid_sus_elements = sum([len(elts) for elts in file2elts.values()])

        del abspath2element
        self.dlog(f"Total valid sus. elements: {total_valid_sus_elements}")
        self.dlog("Suspicious files with sus. elements:")
        for fn, elts in file2elts.items():
            self.dlog(f">>>> ({fn}) {[e['abs_path'] for e in elts]}")

        contexts = []
        assert total_valid_sus_elements > 0
        for fn, info in suspicious_file_infos.items():

            if len(file2elts[fn]) == 0:
                self.dlog.w(f"No sus. element in the sus. file: {fn}")
                continue

            original_content = info["code"]
            original_content_lines = original_content.split("\n")
            context_content_lines = ["\n...\n"] * len(original_content_lines)
            for elt in file2elts[fn]:
                # elt.range +- context_window as context
                elt_start_l_idx = elt["range"]["start_line"] - 1  # 1-based -> 0-based
                elt_end_l_idx = elt["range"]["end_line"] - 1  # 1-based -> 0-based
                ctx_start_l_idx = max(
                    0,
                    elt_start_l_idx - self.__context_window,
                )
                ctx_end_l_idx = min(
                    len(original_content_lines) - 1,
                    elt_end_l_idx + self.__context_window,
                )
                assert ctx_start_l_idx <= ctx_end_l_idx
                assert (
                    ctx_end_l_idx - ctx_start_l_idx + 1
                    <= elt_end_l_idx - elt_start_l_idx + 1 + 2 * self.__context_window
                )
                assert 0 <= ctx_start_l_idx <= len(context_content_lines) - 1
                assert 0 <= ctx_end_l_idx <= len(context_content_lines) - 1

                context_content_lines[ctx_start_l_idx : ctx_end_l_idx + 1] = (
                    original_content_lines[ctx_start_l_idx : ctx_end_l_idx + 1]
                )
            context_content_lines = _collapse_ellipsis(context_content_lines)
            context_content = "\n".join(context_content_lines)

            # self.dlog("==================== CONTEXT (BEGIN) ====================")
            # self.dlog(f"Filename: {fn}")
            # self.dlog(f">>>> len(sus. elements) : {len(file2elts[fn])}")
            # self.dlog(f">>>> sus. elements      : {[e['path'] for e in file2elts[fn]]}")
            # self.dlog(f">>>> len(ori. lines)    : {len(original_content_lines)}")
            # self.dlog(f">>>> len(ctx. lines)    : {len(context_content_lines)}")
            # self.dlog("---------------------------------------------------------")
            # self.dlog(f"Context: \n```\n{context_content}\n```")
            # self.dlog("==================== CONTEXT ( END ) ====================")

            contexts.append(
                {
                    "file": fn,
                    "content": context_content,
                    "original_content": original_content,
                }
            )

        return contexts

    def _get_patches(self, raw_response: str) -> List[str]:
        blocks = _parse_multi_code_blocks(raw_response)
        blocks = [block for block in blocks if block.lang == "diff"]
        if not blocks:
            # Make gemini-3-flash happy
            self.dlog.w(f"No diff block found in the response; Try fix")
            self.dlog.w(f">>>> response: `{raw_response}`")
            lines = raw_response.split("\n")
            new_lines = []
            for line in lines:
                if line.strip().startswith("###"):
                    new_lines.append("```diff")
                new_lines.append(line)
                if line.strip().endswith(">>> REPLACE"):
                    new_lines.append("```")
            raw_response = "\n".join(new_lines)
            self.dlog.w(f">>>> fixed response: `{raw_response}`")
            blocks = _parse_multi_code_blocks(raw_response)

        # try:
        #     for block in blocks:  # let it fail if the format is not correct
        #         assert block.content.strip().startswith("###")
        #         assert "<<<<<<< SEARCH" in block.content
        #         assert "=======" in block.content
        #         assert ">>>>>>> REPLACE" in block.content
        # except AssertionError as ex:
        #     self.dlog.w(f"Incorrect patch format ({ex})")
        #     self.dlog.w(f">>>> response: `{raw_response}`")
        #     raise
        return [block.content for block in blocks]

    def _simple_generate_patch(
        self,
        input: "Input",
        val_results: List[ValidationResult],
        return_dict: Dict[str, Any],
    ) -> Iterator[GeneratedPatch]:
        num_pacthes_to_gen = int(self.__num_pacthes_to_gen)
        # Get issue description: refer to https://github.com/SEC-bench/SWE-agent/blob/main/sweagent/run/batch_instances.py#L275
        issue_description_d: Dict[str, Any] = input.issue_descriptions
        original_issue_description = issue_description_d["original"]
        enhanced_issue_description = issue_description_d["enhanced_issue_description"]
        instance = input.instance
        assert original_issue_description == instance.config.bug_report

        if getenv("ABL_DISABLE_ENHANCED_REPORT_FOR_PG_STAGE", "0") == "1":
            self.dlog.w("[ABL]=================================================+")
            self.dlog.w("[ABL] ABL_DISABLE_ENHANCED_REPORT_FOR_PG_STAGE is set |")
            self.dlog.w("[ABL]=================================================+")
            enhanced_issue_description = original_issue_description
            assert (
                "## Context Analysis written by your co-developer"
                not in enhanced_issue_description
            )
            assert (
                "## Safety Property Analysis Report provided by your co-developer"
                not in enhanced_issue_description
            )

        # 1. Generate & Apply & Test Patch

        ## 1.1 Construct Context (file content with only suspicious elements + arounding lines)
        contexts = self._construct_context(
            input.suspicious_file_infos,
            input.suspicious_elements,
        )
        return_dict["contexts"] = contexts
        context_prompt_sb = StringIO()
        for i, c in enumerate(contexts, start=1):
            pl_type = _detect_PL_from_file_suffix(c["file"])
            context_prompt_sb.write(f">>>> File {i}. {c['file']} <<<<\n")
            context_prompt_sb.write(f"```{pl_type}\n")
            context_prompt_sb.write(c["content"] + "\n")
            context_prompt_sb.write("```\n")
            context_prompt_sb.write(f">>>> END OF FILE {i} <<<<\n")
            context_prompt_sb.write("\n")
        context_prompt = context_prompt_sb.getvalue()

        ## 1.2 Generate Search/Replace Diff Patch
        self.ilog(f"Gen@0 -- Generating the first patch (GREEDY)")
        pg_prompt = SIMPLE_PATCH_GENERATION_PROMPT.format(
            issue_description=enhanced_issue_description,
            suspicious_files=context_prompt,
        )
        pg_messages = []
        pg_messages.append(Message.system(DEFAULT_SYSTEM_PROMPT))
        pg_messages.append(Message.user(pg_prompt))
        self.dlog(f">>>> LLM Request: `{pg_prompt}`")

        INIT_T = float(getenv("SIMPLE_PATCH_GEN_INIT_T", "0.0"))
        if INIT_T != 0.0:
            self.wlog(f"SIMPLE_PATCH_GEN_INIT_T is set to {INIT_T}")

        ### 1.2.1 Greedy one patch
        the_1st_pg_response = self.__model.ask(
            messages=pg_messages,
            temperature=INIT_T,
        )
        self.dlog(f">>>> LLM Response[0]: `{the_1st_pg_response.content}`")
        the_1st_patch = GeneratedPatch(self._get_patches(the_1st_pg_response.content))
        yield the_1st_patch

        ### 1.2.2 Sample multiple patches
        for sa_i in range(num_pacthes_to_gen - 1):
            self.ilog(f"Gen@{sa_i+1} -- Generating the {sa_i+1}-th patch (SAMPLING)")
            the_rem_pg_response = self.__model.ask(
                messages=pg_messages,
                temperature=1,
            )
            self.dlog(f">>>> LLM Response[{sa_i+1}]: `{the_rem_pg_response.content}`")
            a_rem_patch = GeneratedPatch(self._get_patches(the_rem_pg_response.content))
            yield a_rem_patch

    def _select_patch(
        self, val_results: List[ValidationResult]
    ) -> Dict[str, Optional[GeneratedPatch]]:  # strategy -> submission patch or None
        """Select the final patch for submission via majority voting"""

        def _sort(list1, key_list) -> list:
            """Sort list1 by key_list"""
            if len(list1) != len(key_list):
                raise ValueError("list1 and key_list must have the same length")
            merged = list(zip(list1, key_list))
            merged.sort(key=lambda x: x[1])
            return merged

        # Strategy 1: Directly perform majority voting
        def simple_mj_voting():
            patch_candidates = [v.patch for v in val_results]
            assert len(patch_candidates) != 0

            formatted_patched_contents = [
                "\n\n---\n\n".join(
                    [
                        f"<file path={f}>{_format_cpp_code(c)}</file>"
                        for c, f in _sort(patch.patched_file_contents, patch.file_paths)
                    ]
                )
                for patch in patch_candidates
            ]
            patch_count = {}
            for p in formatted_patched_contents:
                if p not in patch_count:
                    patch_count[p] = 0
                patch_count[p] += 1
            max_fmt_patch = max(patch_count, key=patch_count.get)
            patch_score = patch_count[max_fmt_patch]
            patch_scores = sorted(patch_count.values(), reverse=True)
            ph = patch_candidates[formatted_patched_contents.index(max_fmt_patch)]
            voting_details = ph.additional_info.setdefault("patch_select_details", {})
            voting_details["simple_mj_voting.score"] = patch_score
            voting_details["simple_mj_voting.scores"] = patch_scores
            voting_details["simple_mj_voting.submitted"] = True
            return ph

        # Strategy 2: Perform majority voting on patches that pass the PoC
        def mj_voting_on_pocpassing():
            valid_patch_candidates = [v.patch for v in val_results if v.passed]
            if len(valid_patch_candidates) == 0:
                return None

            formatted_patched_contents = [
                "\n\n---\n\n".join(
                    [
                        f"<file path={f}>{_format_cpp_code(c)}</file>"
                        for c, f in _sort(patch.patched_file_contents, patch.file_paths)
                    ]
                )
                for patch in valid_patch_candidates
            ]
            patch_count = {}
            for p in formatted_patched_contents:
                if p not in patch_count:
                    patch_count[p] = 0
                patch_count[p] += 1
            max_fmt_patch = max(patch_count, key=patch_count.get)
            patch_score = patch_count[max_fmt_patch]
            patch_scores = sorted(patch_count.values(), reverse=True)
            ph = valid_patch_candidates[formatted_patched_contents.index(max_fmt_patch)]
            voting_details = ph.additional_info.setdefault("patch_select_details", {})
            voting_details["mj_voting_on_pocpassing.score"] = patch_score
            voting_details["mj_voting_on_pocpassing.scores"] = patch_scores
            voting_details["mj_voting_on_pocpassing.submitted"] = True
            return ph

        return {
            "simple_mj_voting": simple_mj_voting(),
            "mj_voting_on_pocpassing": mj_voting_on_pocpassing(),
        }
