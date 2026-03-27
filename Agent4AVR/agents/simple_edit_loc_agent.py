import re
import os
import json
import time
import shlex
import contextlib
from io import StringIO
from typing import Any, Optional, List, Dict, Tuple, Union
from collections import Counter
from dataclasses import dataclass, asdict, field
from ..basic import LLMQueryRecord
from ..model import Message, Model, ContextLengthExceededException, LLMQueryRecorder
from ..instance import Instance, BuildResult, ReproResult, Patch
from ..vector_store import SimpleVectorStore
from ..rs_utils import (
    _get_exception_tb,
    _get_index_from_line_col,
    _is_valid_c_identifier,
    _extract_c_identifiers,
    _make_temp_file_mgr,
    _str_multi_split_v2,
)
from ..utils import (
    _dlog,
    _get_uuid,
    _parse_code_block,
    _detect_PL_from_file_suffix,
    _analyze_no_attr_python_function_calls,
    _make_enahanced_report_for_clang_static_analyzer,
    _make_enahanced_report_for_facebook_infer,
    _make_enahanced_report_for_cppcheck,
    _parse_search_replace_patch,
    _extract_sanitizer_report,
    _extract_stack_frames_from_sanitizer_log,
)
from .agent import Agent, AgentAskRecord
from .prompts import (
    DEFAULT_SYSTEM_PROMPT,
    PRE_COLLECT_CONTEXT_PROMPT,
    PRE_COLLECT_CONTEXT_PROMPT_FOR_OPENAI_MODELS,
    SAFETY_PROPERTY_ANALYSIS_PROMPT,
    SAFETY_PROPERTY_ANALYSIS_PROMPT_FOR_OPENAI_MODELS,
    OBTAIN_SUSPICIOUS_FILES_PROMPT,
    OBTAIN_IRRELEVANT_FILES_PROMPT,
    OBTAIN_SUSPICIOUS_ELEMENTS_PROMPT,
)


@dataclass
class _ToolCallCtx:
    max_calls: int
    message_when_max_calls_reached: str
    num_calls: int = -1
    agent_actions: List[Dict[str, Any]] = field(default_factory=list)
    agent_tool_calls: List[Dict[str, Any]] = field(default_factory=list)


def _create_on_agent_action_callback(ctx: Any):
    from langchain_core.callbacks import BaseCallbackHandler

    class _ToolCallCounter(BaseCallbackHandler):
        def __init__(self, ctx: Any):
            super().__init__()
            self._ctx = ctx

        def on_agent_action(self, action, *args, run_id, **kwargs) -> Any:
            _dlog(f"[Agent] Action: `{action.tool}`")
            _dlog(f"[Agent] >>>> log: `{action.log.rstrip()}`")
            _dlog(f"[Agent] >>>> increment num_calls to {self._ctx.num_calls + 1}")
            self._ctx.num_calls += 1
            self._ctx.agent_actions.append(
                {"run_id": str(run_id), "action": action.model_dump()}
            )

    if not hasattr(ctx, "num_calls"):
        raise ValueError("ctx must have `num_calls` attribute")
    if not hasattr(ctx, "agent_actions"):
        raise ValueError("ctx must have `agent_actions` attribute")

    return _ToolCallCounter(ctx)


def _create_on_tool_X_callback(ctx: Any):
    from langchain_core.callbacks import BaseCallbackHandler

    class _Callback(BaseCallbackHandler):
        def __init__(self, ctx: Any):
            super().__init__()
            self._ctx = ctx

        def on_tool_start(self, serialized, input_str, *, run_id, **kwargs) -> Any:
            _dlog(f"[Tool] Start: `{serialized['name']}`")
            _dlog(f"[Tool] >>>> run_id: {run_id}")
            _dlog(f"[Tool] >>>> input: `{input_str}`")

            started_at = time.time()
            ctx.agent_tool_calls.append(
                {
                    "run_id": str(run_id),
                    "tool": serialized.copy(),
                    "input": input_str,
                    "started_at": started_at,
                }
            )

            if "start" in os.getenv("STEP_DEBUG_TOOL", ""):
                input("------- Any key to continue ------")

        def on_tool_end(self, output, *, run_id, parent_run_id, **kwargs) -> Any:
            _dlog(f"[Tool] End: `{kwargs['name']}`")
            _dlog(f"[Tool] >>>> run_id: {run_id}")
            _dlog(f"[Tool] >>>> output: \n=========\n{output}\n=========\n")

            ended_at = time.time()
            for call in reversed(ctx.agent_tool_calls):
                if call["run_id"] == str(run_id):
                    assert kwargs["name"] == call["tool"]["name"]
                    call["output"] = output
                    call["ended_at"] = ended_at
                    call["duration"] = ended_at - call["started_at"]
                    break
            else:
                assert False, f"Cannot find tool call with run_id={run_id}"

            if "end" in os.getenv("STEP_DEBUG_TOOL", ""):
                input("------- Any key to continue ------")

    if not hasattr(ctx, "agent_tool_calls"):
        raise ValueError("ctx must have `agent_tool_calls` attribute")

    return _Callback(ctx)


def _make_search_code_toolkit(instance: Instance, ctx: _ToolCallCtx) -> list:
    from langchain.agents import tool

    # states for the toolkit
    MAX_ITERATIONS = int(ctx.max_calls)
    repo_name = instance.repo_path.split("/")[-1]
    file_info_cache = {}

    hint_for_element_not_found = ""
    if getattr(ctx, "enable_code_symbol_analysis_toolkit", False):
        hint_for_element_not_found += "\nYou can try other tools. For example, if you know where the element you are looking for is used, you can use `resolve_code_symbol` to find its definition."

    @tool(
        "search_code_element_in_file",
        description=f"""Search for a code element (e.g., function, class, struct, union, enum, macro, global_variable) in a C/C++ file.
        Args:
            element_name: The name of the code element to search for (e.g., function name).
            file_path: The path to the C/C++ file in the repository (start from repo name "{repo_name}", e.g., {repo_name}/folder1/file1.{{c,h,cpp,cc,cxx,C,hpp,hh,hxx,H}})
            mark_lines: The line numbers to mark in the returned code (e.g., [10, 20, 30], or [] if no mark lines). These lines will be marked with "// <<<<< {repo_name}/folder1/file1.c:10" in the returned code. You can use this to highlight the KEY lines of the code element (e.g., the crash point).
        Returns:
            The information of the code element, including type, code, and start/end line number.
            If the code element is not found, return "Element Not Found".
            If multiple code elements are found, return them.
            If some error occurs, return "Error: ...".
        """,
    )
    def search_code_element_in_file(
        element_name: str,
        file_path: str,
        mark_lines: List[int],
    ) -> str:
        if ctx.num_calls > MAX_ITERATIONS:
            return f"Error: {ctx.message_when_max_calls_reached}"

        if not file_path.strip().startswith(f"{repo_name}/"):
            return f"Error: file path '{file_path}' does not start with '{repo_name}/'"
        file_path = file_path.strip().removeprefix(f"{repo_name}/")
        element_name = element_name.strip().strip("'\"")
        if not instance.read_file(f"{instance.repo_path}/{file_path}"):
            file_path = f"{instance.repo_name}/{file_path}"  # add a name back
            if not instance.read_file(f"{instance.repo_path}/{file_path}"):
                return f"Error: file path '{repo_name}/{file_path}' does not exist"

        if file_path not in file_info_cache:
            try:
                info = instance.get_code_structure(files=[file_path])
            except Exception:
                _dlog.w(f"Failed to parse code structure for file {file_path}")
                return f"Error: Failed to parse code structure for file {file_path}. You can read the file content by using the 'read_code_in_file' tool."
            file_info_cache.update(info)
        file_info = file_info_cache[file_path]
        file_info_cache.clear()  # do not use cache

        def _search(elts: list | None):
            results = []
            for elt in elts or []:
                if elt["name"] == element_name:
                    results.append(elt)
                if "children" in elt:
                    results.extend(_search(elt["children"]))
            return results

        results = _search(file_info["elements"])
        if not results:
            return (
                "Element Not Found"
                + (
                    "\n\tHint: Note that the header file parsing might be incomplete. You can try searching for the element you want using other tools."
                    if ".h" in file_path.lower()
                    else ""
                )
                + hint_for_element_not_found
            )
        result_sb = StringIO()
        result_sb.write(
            f"Found {len(results)} element(s) named '{element_name}' in file '{repo_name}/{file_path}':\n"
        )
        for i, elt in enumerate(results, start=1):
            code_lines = elt["code"].split("\n")
            result_sb.write(
                f"{i}. {elt['type']} {element_name} (line range: {elt['range']['start_line']}-{elt['range']['end_line']})\n"
            )
            result_sb.write(f"```{_detect_PL_from_file_suffix(file_path) or ''}\n")
            for lineno, line in enumerate(code_lines, start=elt["range"]["start_line"]):
                result_sb.write(line)
                if lineno in mark_lines:
                    result_sb.write(f" // <<<<< {repo_name}/{file_path}:{lineno}")
                result_sb.write("\n")
            result_sb.write("```\n")
        return result_sb.getvalue().strip()

    @tool(
        "read_code_in_file",
        description=f"""\
Read the code in a file.
Args:
    file_path: The path to the file in the repository (start from repo name "{repo_name}", e.g., {repo_name}/folder1/file1.{{c,h,cpp,cc,cxx,C,hpp,hh,hxx,H}})
    start_line: The start line number to read (inclusive).
    end_line: The end line number to read (inclusive). Must be >= start_line.
    context_lines: The number of extra lines to read before start_line and after end_line.
    mark_lines: The line numbers to mark in the returned code (e.g., [10, 20, 30], or [] if no mark lines). These lines will be marked with " // <<<<< {repo_name}/folder1/file1.c:10" in the returned code. You can use this to highlight the KEY lines of the code element (e.g., the crash point).
Returns:
    The code in the file from max(1, start_line - context_lines) to min(total_lines, end_line + context_lines).
    If `mark_lines` is not empty, the marked lines will be marked with "// <<<<< ..." in the returned code.
Examples:
    - read_code_in_file(file_path="{repo_name}/folder1/file1.c", start_line=50, end_line=60, context_lines=5, mark_lines=[55, 60])  # Reads lines 45-65; marks line 55 and 60 with special comments
    - read_code_in_file(file_path="{repo_name}/folder1/file2.c", start_line=50, end_line=50, context_lines=10, mark_lines=[]) # Reads lines 40-60 (single line with context)
    - read_code_in_file(file_path="{repo_name}/folder1/subfolder1/file3.c", start_line=10, end_line=30, context_lines=0, mark_lines=[])  # Reads exactly lines 10-30""",
    )
    def _read_code_in_file_v2(
        file_path: str,
        start_line: int,
        end_line: int,
        context_lines: int,
        mark_lines: List[int],
    ) -> str:
        raise NotImplementedError("do not use this tool")
        if ctx.num_calls > MAX_ITERATIONS:
            return f"Error: {ctx.message_when_max_calls_reached}"

        if not file_path.strip().startswith(f"{repo_name}/"):
            return f"Error: file path '{file_path}' does not start with '{repo_name}/'"
        file_path = file_path.strip().removeprefix(f"{repo_name}/")
        content = instance.read_file(f"{instance.repo_path}/{file_path}")
        if not content:
            return f"Error: file path '{repo_name}/{file_path}' does not exist"
        if start_line > end_line:
            # do not raise error, just swap them
            start_line, end_line = end_line, start_line
        if context_lines < 0:
            # do not raise error, just set it to 0
            context_lines = 0
        assert start_line <= end_line
        assert context_lines >= 0

        lines = content.split("\n")
        start_line = max(1, start_line - context_lines)
        end_line = min(len(lines), end_line + context_lines)
        result_sb = StringIO()
        result_sb.write(f"{repo_name}/{file_path}:{start_line}-{end_line}:\n")
        result_sb.write(f"```{_detect_PL_from_file_suffix(file_path) or ''}\n")
        for lineno, line in enumerate(
            lines[start_line - 1 : (end_line - 1) + 1], start=start_line
        ):
            result_sb.write(line)
            if lineno in mark_lines:
                result_sb.write(f" // <<<<< {repo_name}/{file_path}:{lineno}")
            result_sb.write("\n")
        result_sb.write("```\n")
        return result_sb.getvalue().strip()

    @tool(
        "read_code_in_file",
        description=f"""Read the code in a file.
        Args:
            file_path: The path to the file in the repository (start from repo name "{repo_name}", e.g., {repo_name}/folder1/file1.{{c,h,cpp,cc,cxx,C,hpp,hh,hxx,H}})
            center_line: The line number to read.
            context_lines: The number of lines to read before and after the `center_line`.
            mark_lines: The line numbers to mark in the returned code (e.g., [10, 20, 30], or [] if no mark lines). These lines will be marked with "// <<<<< {repo_name}/folder1/file1.c:10" in the returned code. You can use this to highlight the KEY lines of the code element (e.g., the crash point).
        Returns:
            The code in the file from center_line - context_lines to center_line + context_lines.
            If `mark_lines` is not empty, the marked lines will be marked with "// ..." in the returned code.
        """,
    )
    def read_code_in_file(
        file_path: str,
        center_line: int,
        context_lines: int,
        mark_lines: List[int],
    ) -> str:
        if ctx.num_calls > MAX_ITERATIONS:
            return f"Error: {ctx.message_when_max_calls_reached}"

        if not file_path.strip().startswith(f"{repo_name}/"):
            return f"Error: file path '{file_path}' does not start with '{repo_name}/'"
        file_path = file_path.strip().removeprefix(f"{repo_name}/")
        content = instance.read_file(f"{instance.repo_path}/{file_path}")
        if not content:
            file_path = f"{instance.repo_name}/{file_path}"  # add a name back
            content = instance.read_file(f"{instance.repo_path}/{file_path}")
        if not content:
            return f"Error: file path '{repo_name}/{file_path}' does not exist"
        lines = content.split("\n")
        start_line = max(1, center_line - context_lines)
        end_line = min(len(lines), center_line + context_lines)
        result_sb = StringIO()
        result_sb.write(f"{repo_name}/{file_path}:{start_line}-{end_line}:\n")
        result_sb.write(f"```{_detect_PL_from_file_suffix(file_path) or ''}\n")
        for lineno, line in enumerate(
            lines[start_line - 1 : end_line],
            start=start_line,
        ):
            result_sb.write(line)
            if lineno in mark_lines:
                result_sb.write(f" // <<<<< {repo_name}/{file_path}:{lineno}")
            result_sb.write("\n")
        result_sb.write("```\n")
        return result_sb.getvalue().strip()

    # if getattr(ctx, "enable_search_code_toolkit_v2", False):
    #     return [_read_code_in_file_v2]
    # else:
    if True:
        return [search_code_element_in_file, read_code_in_file]


def _make_code_sysmbol_analysis_toolkit(instance, ctx) -> list:
    from langchain.agents import tool

    class _FakeSelf:
        def __init__(self):
            self.dlog = _dlog

    self = _FakeSelf()

    if not ctx.enable_code_symbol_analysis_toolkit:
        self.dlog.w(
            "Code Symbol Analysis Toolkit is disabled. "
            "Set ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT=1 to enable it."
        )
        return []

    if not ctx.enable_code_symbol_analysis_toolkit_v2:
        raise NotImplementedError("only support v2")

    FIND_DEFINITION = "FIND_DEFINITION"
    FIND_REFERENCES = "FIND_REFERENCES"

    AVAILABLE_OPERATIONS = [FIND_DEFINITION, FIND_REFERENCES]

    def _parse_code_symbol_markers(
        file_rel_paths: List[str],
        old_contents: List[str],
        new_contents: List[str],
    ) -> Tuple[Optional[list], Optional[str]]:
        ## [(file_idx, file, line, op, start_col, end_col)], 1-based, [start_col, end_col)
        code_symbol_markers = []
        for fn_i, (rel_fn, old, new) in enumerate(
            zip(file_rel_paths, old_contents, new_contents, strict=True)
        ):
            old_lines, new_lines = old.split("\n"), new.split("\n")
            assert len(old_lines) == len(new_lines)
            for lineno, (old_line, new_line) in enumerate(
                zip(old_lines, new_lines), start=1  # 1-based linenos
            ):
                if all(m not in new_line for m in AVAILABLE_OPERATIONS):
                    if re.sub(r"\s+", "[S]", old_line) == re.sub(
                        r"\s+", "[S]", new_line
                    ):
                        continue
                    if old_line.strip() != new_line.strip():
                        return (
                            None,
                            f"Error: Invalid query format; the line '{old_line}' in file '{ctx.repo_name}/{rel_fn}' is changed to '{new_line}' but no marker is found.",
                        )

                ## tokenize
                @dataclass
                class _Token:
                    token: str
                    col_idx: int

                    def __repr__(self):
                        return f"({repr(self.token)}, {self.col_idx})"

                END_TK = "$END$"
                parts = _str_multi_split_v2(new_line, AVAILABLE_OPERATIONS)
                old_tokens = [_Token(t, i) for i, t in enumerate(old_line)]
                new_tokens = []
                for p in parts:
                    col_idx = sum(len(t.token) for t in new_tokens)
                    if p in AVAILABLE_OPERATIONS:
                        new_tokens.append(_Token(p, col_idx))
                    else:
                        new_tokens.extend(
                            [_Token(t, col_idx + i) for i, t in enumerate(p)]
                        )
                old_tokens.append(_Token(END_TK, len(old_line)))
                new_tokens.append(_Token(END_TK, len(new_line)))

                ## ignore whitespace tokens
                old_tokens = [tk for tk in old_tokens if not tk.token.isspace()]
                new_tokens = [tk for tk in new_tokens if not tk.token.isspace()]

                ## match
                stack = [(None, None)]
                old_i, new_i = 0, 0
                while old_i < len(old_tokens) and new_i < len(new_tokens):
                    if new_tokens[new_i].token in AVAILABLE_OPERATIONS:
                        stack.append((new_tokens[new_i].token, old_i))
                        new_i += 1
                    elif stack[-1][0] in AVAILABLE_OPERATIONS:
                        if new_tokens[new_i].token != "(":
                            return (
                                None,
                                f"Error: Invalid query format; the line '{new_line}' in file '{ctx.repo_name}/{rel_fn}' expects '(' after '{stack[-1]}'.",
                            )
                        stack.append(("(", old_i))
                        new_i += 1
                    elif stack[-1][0] == "(" and new_tokens[new_i].token == ")":
                        top1, top1_old_idx = stack.pop()
                        top2, top2_old_idx = stack.pop()
                        assert top1 == "("
                        assert top2 in AVAILABLE_OPERATIONS
                        assert top1_old_idx == top2_old_idx
                        code_symbol_markers.append(
                            (
                                fn_i,
                                rel_fn,
                                lineno,  # 1-based
                                top2,  # op
                                # 0-based -> 1-based
                                old_tokens[top1_old_idx].col_idx + 1,
                                # ] -> ),0-based -> 1-based
                                (old_tokens[old_i - 1].col_idx + 1) + 1,
                            )
                        )
                        new_i += 1
                    elif old_tokens[old_i].token == new_tokens[new_i].token:
                        old_i += 1
                        new_i += 1
                    else:
                        return None, (
                            f"Error: Invalid query format. The line '{new_line}' in file '{ctx.repo_name}/{rel_fn}' does not match the original line '{old_line}'\n"
                            + f"When creating SEARCH/REPLACE edit-style queries, you MUST NOT add or remove any code other than the magic macro calls (i.e., {AVAILABLE_OPERATIONS})."
                        )

        return code_symbol_markers, None

    @tool(
        "resolve_code_symbol",
        description=f"""\
Analyzes code symbols to retrieve their definitions, references, and other relevant information.

This tool processes a batch of queries and returns results for all of them.
Each query is an *edit* that marks the target code symbol and specifies the analysis operation by modifying a single location in a single file, following a strict format. Edits are grouped by file and applied sequentially to each file.
Important:
- These edits are virtual—they do not actually modify the repository code, but are only used to mark the symbols to be analyzed.
- If you want to actually modify the repository code, you should use `apply_edits` tool.

**How It Works:**
To analyze a code symbol, you need to wrap the target code symbol (e.g., a function name, a variable name, etc.) with a magic macro call (e.g., `{FIND_DEFINITION}(...)` or `{FIND_REFERENCES}(...)`) that specifies the desired operation. 
The tool will parse these markers from the virtual—modified code and perform the corresponding analysis on the original repository.

Supported Operations:
- {FIND_DEFINITION}: Finds the definition of a code symbol and returns the full definition code of the marked symbol.
- {FIND_REFERENCES}: Finds all references to a code symbol and returns all code lines that reference the marked symbol.

Args:
queries (List[str]): A list of strings, each representing a complete *SEARCH/REPLACE* edit-style query string that strictly follows the required format.

Returns:
str: Symbol analysis results for all queries.

Querying Process (pseudocode):

```python
@dataclass
class CodeSymbolMarker:
operation: str
file: str
line: int
column: int

code_symbol_markers: List[CodeSymbolMarker] = []
for filename, edits_in_a_file in parse_and_group_edit_by_file(queries):
old_content: str = read_file_from_repo(filename)
new_content: str = old_content
for single_edit in edits_in_a_file:
search: str = single_edit.search
replace: str = single_edit.replace
new_content: str = new_content.replace(search, replace)
code_symbol_markers.extend(parse_code_symbol_markers(filename, new_content))

results = []
for marker in code_symbol_markers:
if marker.operation == '{FIND_DEFINITION}':
results.append(find_definition_from_repo(marker.file, marker.line, marker.column))
elif marker.operation == '{FIND_REFERENCES}':
results.append(find_references_from_repo(marker.file, marker.line, marker.column))
elif ... (other supported operations) ...
...

print(show_code_symbol_analysis_results(results))
```

Every *SEARCH/REPLACE* edit-style query must strictly follow this format:
1. The file path
2. Start of search block: <<<<<<< SEARCH
3. A contiguous chunk of lines to search for in the existing source code
4. The dividing line: =======
5. The lines to replace into the source code
6. End of replace block: >>>>>>> REPLACE
7. Must contain at least one code symbol marker
- Each marker is a macro-call-like expression, e.g., {FIND_DEFINITION}(symbol_name)
- The "macro" name corresponds to the operation (e.g., {FIND_DEFINITION} or {FIND_REFERENCES})
- Wrap ONLY the identifier you want to resolve, not the entire expression

Important Notes:
- Each edit-style query (i.e., queries[i]) must target exactly one file and one code location.
- Multiple queries can be applied to the same file or different files.
- Each query can contain multiple code symbol markers.
- When marking symbols in complex expressions, only wrap the specific identifier, not the entire expression.
* For struct/class member access: use `obj->{FIND_DEFINITION}(member)` or `obj.{FIND_REFERENCES}(field)`
* For function calls: use `{FIND_DEFINITION}(function_name)(arguments)`
* For type names: use `{FIND_DEFINITION}(TypeName) variable_name;`
- When creating SEARCH/REPLACE edit-style queries, you MUST NOT add or remove any code other than the magic macro calls.
* The line count and all code content must remain identical between SEARCH and REPLACE blocks—only the specific code symbols should be wrapped with magic macros.
* The magic macro name, left and right parentheses, must be on the same line as the code symbol to resolve.

Example Queries:

<example1_variable_reference>
### {ctx.repo_name}/path/to/example1.c
<<<<<<< SEARCH
int main() {{
int count = 0;
count = count + 1;
printf("Count: %d", count);
=======
int main() {{
int count = 0;
count = {FIND_REFERENCES}(count) + 1;
printf("Count: %d", count);
>>>>>>> REPLACE
</example1_variable_reference>

<example2_struct_member_access>
### {ctx.repo_name}/path/to/example2.c
<<<<<<< SEARCH
if (data->size > 0) {{
data->buffer = malloc(data->size);
data->flags |= DATA_ALLOCATED;
=======
if (data->{FIND_DEFINITION}(size) > 0) {{
data->buffer = malloc(data->size);
data->{FIND_REFERENCES}(flags) |= DATA_ALLOCATED;
>>>>>>> REPLACE
</example2_struct_member_access>

<example3_function_call_and_type>
### {ctx.repo_name}/path/to/example3.c
<<<<<<< SEARCH
void calculate_stats(Stats* stats) {{
int result = compute_value(stats->data, stats->length);
stats->average = result / stats->count;
validate_stats(stats);
}}
=======
void calculate_stats({FIND_DEFINITION}(Stats)* stats) {{
int result = {FIND_DEFINITION}(compute_value)(stats->data, stats->length);
stats->average = result / stats->count;
{FIND_REFERENCES}(validate_stats)(stats);
}}
>>>>>>> REPLACE
</example3_function_call_and_type>

<example4_class_object_access>
### {ctx.repo_name}/path/to/example4.c
<<<<<<< SEARCH
void Manager::initialize() {{
config.load("settings.conf");
config.timeout = DEFAULT_TIMEOUT;
logger.info("Initialized with timeout: " + std::to_string(config.timeout));
}}
=======
void Manager::initialize() {{
config.{FIND_DEFINITION}(load)("settings.conf");
config.{FIND_REFERENCES}(timeout) = DEFAULT_TIMEOUT;
logger.info("Initialized with timeout: " + std::to_string(config.timeout));
}}
>>>>>>> REPLACE
</example4_class_object_access>

<example5_multiple_operations_single_query>
### {ctx.repo_name}/path/to/example5.c
<<<<<<< SEARCH
Connection* create_connection(Endpoint* ep) {{
Connection* conn = alloc_connection();
conn->fd = open_socket();
conn->state = STATE_CONNECTING;
setup_connection(conn, ep->address, ep->port);
=======
{FIND_DEFINITION}(Connection)* create_connection({FIND_REFERENCES}(Endpoint)* ep) {{
Connection* conn = {FIND_DEFINITION}(alloc_connection)();
conn->fd = {FIND_REFERENCES}(open_socket)();
conn->{FIND_DEFINITION}(state) = STATE_CONNECTING;
{FIND_REFERENCES}(setup_connection)(conn, ep->address, ep->port);
>>>>>>> REPLACE
</example5_multiple_operations_single_query>

<example6_find_references_to_a_struct>
### {ctx.repo_name}/path/to/example6.c
<<<<<<< SEARCH
struct Node {{
uint32_t index;
void *data;
}};
=======
struct FIND_REFERENCES(Node) {{
uint32_t index;
void *data;
}};
>>>>>>> REPLACE
</example6_find_references_to_a_struct>

Formatting Requirements:
- *SEARCH/REPLACE* edit-style queries REQUIRE PROPER INDENTATION. For example, to add the line '        printf(x)', you must include all leading spaces.
- Provide sufficient search context (at least 3 lines of code) to ensure the code location can be accurately matched.
- Do not use "..." or any other placeholder to omit original code content. You must preserve the original code format and content in the *SEARCH/REPLACE* edit-style query.
- Each *SEARCH/REPLACE* edit must start with the file path in the format: '### {ctx.repo_name}/path/to/a/file.c'
- The file path must point to a C/C++ file in the repository (starting from the repo name "{ctx.repo_name}", e.g., {ctx.repo_name}/folder1/file1.{{c,h,cpp,cc,cxx,C,hpp,hh,hxx,H}})
- If multiple queries are needed, provide them as a list of strings (i.e., queries: List[str])
- ONLY wrap the identifier name in the magic macro, not the entire expression
""",
        # **Comparison with `search_code_element_in_file`:**
        # This tool performs precise semantic analysis through code location markers, while `search_code_element_in_file` conducts broader textual search by element names.
        # Key differences in usage scenarios:
        # - **Use `analyze_code_symbols`** when you know **where a symbol is used** and need to find its definition or references - regardless of whether you know which file contains the definition
        # - **Use `search_code_element_in_file`** when you **know the definition file** and want to find the implementation of this code element
        # """,
    )
    def _resolve_code_symbol(queries: Union[List[str], str]) -> str:
        if isinstance(queries, str):
            queries = [queries]

        if ctx.num_calls > ctx.max_calls:
            return f"Error: {ctx.message_when_max_calls_reached}"

        self.dlog("\n\n".join(["Queries: ```", *queries, "```"]))

        # Check edit format
        self.dlog("Checking edits format ...")
        try:
            # sr_patches = _parse_search_replace_patch(queries)
            sr_patches = _parse_search_replace_patch(queries, remove_line_marker=True)
        except ValueError as ex:
            return f"Error: Invalid query format; the provided search-replace edit-style queries are invalid. Details: {ex}"

        # Check files to be edited
        self.dlog("Checking to-v-edit files ...")
        old_contents = []
        file_rel_paths = []
        for srp in sr_patches:
            if not srp.file.startswith(f"{ctx.repo_name}/"):
                return f"Error: Apply edits failed; the file path '{srp.file}' does not start with the repo name '{ctx.repo_name}'."
            rel_file_path = srp.file.removeprefix(f"{ctx.repo_name}/")
            file_rel_paths.append(rel_file_path)
            abs_file_path = os.path.join(instance.repo_path, rel_file_path)
            content = instance.read_file(abs_file_path)
            if content is None:
                srp.file = f"{ctx.repo_name}/{srp.file}"  # add a name
                rel_file_path = srp.file.removeprefix(f"{ctx.repo_name}/")
                abs_file_path = os.path.join(instance.repo_path, rel_file_path)
                content = instance.read_file(abs_file_path)
            if content is None:
                return f"Error: Invalid query format; the file path '{srp.file}' does not exist."
            old_contents.append(content)
        assert len(file_rel_paths) == len(set(file_rel_paths))

        # Virtually apply edits

        def _whitespace_insensitive_replace(text, old, new):
            import re

            parts = re.split(r"\s+", old.strip())
            pattern = r"\s+".join(re.escape(part) for part in parts)

            regex = re.compile(pattern)
            return regex.sub(new, text)

        ## Calcu new content for each file
        self.dlog("Calculating new content for each file ...")
        new_contents = []
        edit_details = {}  # index => details
        file_edit_details = {}  # filename => details
        for ctt, sr_patch in zip(old_contents, sr_patches, strict=True):
            assert ctt is not None
            patched_ctt = ctt
            for diff in sr_patch.patches:
                index: int = diff.index
                # patched_ctt_1 = patched_ctt.replace(diff.search, diff.replace)
                try:
                    patched_ctt_1 = _whitespace_insensitive_replace(
                        text=patched_ctt, old=diff.search, new=diff.replace
                    )
                except Exception as ex:
                    self.dlog(f"_whitespace_insensitive_replace: error :{ex}")
                    patched_ctt_1 = patched_ctt.replace(diff.search, diff.replace)
                edit_details[index] = {"edited": (patched_ctt != patched_ctt_1)}
                patched_ctt = patched_ctt_1
            file_edit_details[sr_patch.file] = {"edited": ctt != patched_ctt}
            new_contents.append(patched_ctt)
        assert len(file_edit_details) == len(old_contents) == len(sr_patches)
        assert len(edit_details) == sum([len(p.patches) for p in sr_patches])

        ## Check if any edit is applied
        self.dlog("Checking if any edit is applied ...")
        any_edited = any(v["edited"] for v in edit_details.values())
        any_file_edited = any(v["edited"] for v in file_edit_details.values())
        if not any_edited:
            assert not any_file_edited
            return f"Error: Invalid query format; no content change detected after trying to virtually apply the edits. Details: {edit_details}"

        ## Check line count before and after edit
        self.dlog("Checking line count before and after edit ...")
        assert len(file_rel_paths) == len(old_contents) == len(new_contents)
        line_count_changed_files = []
        for rel_fn, old, new in zip(file_rel_paths, old_contents, new_contents):
            old_lines = old.split("\n")
            new_lines = new.split("\n")
            if len(old_lines) != len(new_lines):
                line_count_changed_files.append(f"{ctx.repo_name}/{rel_fn}")
        if line_count_changed_files:
            return (
                f"Error: Invalid query format; line count changed in files: {line_count_changed_files}\n"
                + "The line count before and after edit must be the same in all files.\n"
                "When creating SEARCH/REPLACE edit-style queries, you MUST NOT add or remove any code other than the magic macro calls."
            )

        # Parse marker (based on Pushdown Automaton)
        self.dlog("Parsing marker ...")
        _code_symbol_markers, error_msg = _parse_code_symbol_markers(
            file_rel_paths=file_rel_paths,
            old_contents=old_contents,
            new_contents=new_contents,
        )
        if error_msg is not None:
            return error_msg
        assert _code_symbol_markers is not None
        self.dlog(f"_code_symbol_markers: {_code_symbol_markers}")

        code_symbol_markers = []
        for fn_i, rel_fn, line, op, start_col, end_col in _code_symbol_markers:
            assert file_rel_paths[fn_i] == rel_fn
            file_content = old_contents[fn_i]
            file_lines = file_content.split("\n")
            line_content = file_lines[line - 1]  # 1-based -> 0-based
            assert 1 <= line <= len(file_lines)
            assert 1 <= start_col <= end_col <= len(line_content) + 1
            symbol = line_content[start_col - 1 : end_col - 1]  # -> 0-based
            # empty
            if not symbol:
                return f"Error: Invalid query format. Empty identifier in {op}() marker at line `{line_content}` of file '{ctx.repo_name}/{rel_fn}' is not allowed."
            # invlid C/C++ symbol
            identifiers = _extract_c_identifiers(symbol)
            assert all(_is_valid_c_identifier(e[0]) for e in identifiers)

            if len(identifiers) > 1:
                self.dlog.w(f"`{symbol}` -> {[it for it, _, _ in identifiers]}")

            for idt, start_idx, end_idx in identifiers:
                # [start_idx, end_idx)
                start_glo_col = start_col + start_idx
                end_glo_col = start_col + end_idx
                assert start_col <= start_glo_col < end_glo_col <= end_col
                assert 1 <= start_glo_col < len(line_content) + 1
                assert 1 <= end_glo_col <= len(line_content) + 1
                assert idt == symbol[start_idx:end_idx]
                assert idt == line_content[start_glo_col - 1 : end_glo_col - 1]
                code_symbol_markers.append(
                    (fn_i, rel_fn, line, op, start_glo_col, end_glo_col)
                )
        if len(_code_symbol_markers) != len(code_symbol_markers):
            self.dlog.w(f"len(_code_symbol_markers) = {len(_code_symbol_markers)}")
            self.dlog.w(f"len(code_symbol_markers) = {len(code_symbol_markers)}")
        del _code_symbol_markers
        if len(code_symbol_markers) > 10:
            old_code_symbol_marker_cnt = len(code_symbol_markers)
            code_symbol_markers = code_symbol_markers[:10]
            self.dlog.w(
                f"Truncated code_symbol_markers: {old_code_symbol_marker_cnt} -> {len(code_symbol_markers)}"
            )

        request_lsp_args = []
        for fn_i, rel_fn, line, op, start_col, end_col in code_symbol_markers:
            assert file_rel_paths[fn_i] == rel_fn
            file_content = old_contents[fn_i]
            file_lines = file_content.split("\n")
            line_content = file_lines[line - 1]  # 1-based -> 0-based
            assert 1 <= line <= len(file_lines)
            assert 1 <= start_col <= end_col <= len(line_content) + 1
            symbol = line_content[start_col - 1 : end_col - 1]  # -> 0-based
            # empty
            assert not not symbol
            # invlid C/C++ symbol
            assert _is_valid_c_identifier(symbol)
            request_lsp_args.append(
                [
                    f"--operation {op.lower()}",
                    f"--file {shlex.quote(rel_fn)}",
                    f"--line {line-1}",
                    f"--column {start_col-1}",
                ]
            )

        lsp_responses = []
        for i, args in enumerate(request_lsp_args):
            cmd = f'bash {instance.config.REPO_TOOLKIT_DIR}/request_lsp.sh {" ".join(args)}'
            self.dlog(f"Request LSP [{i}]: {cmd}")
            lsp_resp = json.loads(
                instance.communicate(
                    cmd,
                    timeout=None,
                    check="raise",
                    error_msg=f"Failed to request LSP with `{args}`",
                )
            )

            if lsp_resp["type"] == "error":
                _msg = lsp_resp["message"]
                if "Unsupported encoding" in _msg:
                    _msg += (
                        "\nThis tool is usable for this file; use other tools instead."
                    )
                return f"Error: {_msg}"

            lsp_responses.append(lsp_resp)
        assert len(lsp_responses) == len(code_symbol_markers)

        def _show_code_symbol_markers(code_symbol_markers: list) -> List[str]:
            results = []
            for index, marker in enumerate(code_symbol_markers):
                fn_i, rel_fn, line, op, start_col, end_col = marker
                fn_to_show = f"{ctx.repo_name}/{rel_fn}"
                start_col_idx = start_col - 1  # 1-based -> 0-based
                end_col_idx = end_col - 1  # 1-based -> 0-based
                num_symbol_chars = end_col_idx - start_col_idx
                symbol_line = old_contents[fn_i].split("\n")[line - 1]
                # FIXME: correctly process not-a-space-width characters, e.g., tab, etc.
                marking_s_line = " " * start_col_idx + "^" * num_symbol_chars
                operation_s_line = " " * start_col_idx + f"({op})"
                # be like:
                ## next = value2->data.u.next; // <<<<< {repo_name}/{file_path}:{lineno}
                ##                ^^^^
                ##                (FIND_DEFINITION)
                view = "\n".join(
                    [
                        f"{symbol_line} // <<<<< {fn_to_show}:{line}",
                        f"{marking_s_line}",
                        f"{operation_s_line}",
                    ]
                )
                results.append(view)
            return results

        def _show_lsp_responses(lsp_responses: list) -> str:

            def _make_single_definition_response(d: dict) -> dict:

                abs_file_path = d["absolutePath"]
                rel_file_path = d["relativePath"]
                file_path_to_show = f"{ctx.repo_name}/{rel_file_path}"

                text = instance.read_file(abs_file_path)
                if text is None:
                    self.dlog.w(f"File not exists: {abs_file_path}")
                    return f"{index}. Error: Found this symbol in {file_path_to_show}, but failed to read the file."

                lo_idx = _get_index_from_line_col(
                    text=text,
                    line=d["range"]["start"]["line"],  # 0-based
                    col=d["range"]["start"]["character"],  # 0-based
                )
                hi_idx = _get_index_from_line_col(
                    text=text,
                    line=d["range"]["end"]["line"],  # 0-based
                    col=d["range"]["end"]["character"],  # 0-based
                )
                symbol_name = text[lo_idx:hi_idx]

                lsp_resp = json.loads(
                    instance.communicate(
                        f"bash {instance.config.REPO_TOOLKIT_DIR}/request_lsp.sh --operation document_symbols --file {shlex.quote(rel_file_path)}",
                        timeout=None,
                        check="raise",
                        error_msg=f"Failed to request LSP document_symbols for {abs_file_path}",
                    )
                )
                if lsp_resp["type"] == "document_symbols":
                    symbols, _ = lsp_resp["result"]
                else:
                    symbols = []

                filtered_symbols = []
                self.dlog(f"Finding {symbol_name} from {abs_file_path} ...")
                # self.dlog(f">>>> target location: `{json.dumps(d, indent=2)}`")
                for sym in symbols:
                    _sym_def_range = sym["location"]["range"]
                    _sym_name: str = sym["name"]
                    if (
                        d["range"]["start"]["line"] >= _sym_def_range["start"]["line"]
                        and d["range"]["end"]["line"] <= _sym_def_range["end"]["line"]
                        and (
                            _sym_name == symbol_name
                            or _sym_name.startswith(symbol_name)
                            or _sym_name.endswith(symbol_name)
                            or symbol_name.startswith(_sym_name)
                            or symbol_name.endswith(_sym_name)
                        )
                    ):
                        filtered_symbols.append(sym)
                # self.dlog(f">>>> #filtered_symbols: {len(filtered_symbols)}")

                if len(filtered_symbols) == 0:
                    self.dlog.w(
                        f">>>> Not found symbol `{symbol_name}` in {file_path_to_show}"
                    )
                    filtered_symbols = [  # dummy symbol, show 1+2*3 lines
                        {
                            "location": {
                                "range": {
                                    "start": {
                                        "line": d["range"]["start"]["line"] - 3,
                                        "character": 0,
                                    },
                                    "end": {
                                        "line": d["range"]["end"]["line"] + 4,
                                        "character": 0,
                                    },
                                }
                            }
                        }
                    ]

                if len(filtered_symbols) != 1:
                    self.dlog.w(
                        f">>>> Found multiple symbols {filtered_symbols} for `{symbol_name}` in {file_path_to_show}"
                    )
                    _fixed_filtered_symbols = [
                        sym_candidate
                        for sym_candidate in filtered_symbols
                        if sym_candidate["name"] == symbol_name
                    ]
                    if _fixed_filtered_symbols:
                        self.dlog.w(">>>>>> found exact-name-matched symbol")
                        filtered_symbols = _fixed_filtered_symbols.copy()
                    else:
                        self.dlog.w(">>>>>> not found exact name match ...")
                    del _fixed_filtered_symbols

                if len(filtered_symbols) != 1:
                    self.dlog.w(
                        f">>>> Found multiple symbols {filtered_symbols} for `{symbol_name}` in {file_path_to_show}"
                    )
                    self.dlog.w(">>>>>> selecting the min-range one ...")
                    filtered_symbols = [
                        min(
                            filtered_symbols,
                            key=lambda x: (
                                x["location"]["range"]["end"]["line"]
                                - x["location"]["range"]["start"]["line"]
                            ),
                        )
                    ]

                if len(filtered_symbols) != 1:
                    # not reachable
                    raise RuntimeError(
                        "Failed to get definition\n"
                        + f" - Symbol: {symbol_name}\n"
                        + f" - Filtered symbols: {filtered_symbols}"
                    )

                self.dlog(
                    f"Found a symbol at target location"
                    # + f"`{json.dumps(filtered_symbols[0], indent=2)}`"
                )

                # Extend context
                def_loc_range = filtered_symbols[0]["location"]["range"]
                filtered_symbols = [
                    {
                        "location": {
                            "range": {
                                "start": {
                                    "line": def_loc_range["start"]["line"] - 10,
                                    "character": 0,
                                },
                                "end": {
                                    "line": def_loc_range["end"]["line"] + 11,
                                    "character": 0,
                                },
                            }
                        }
                    }
                ]
                del def_loc_range

                symbol_line_idx = d["range"]["start"]["line"]
                def_loc_range = filtered_symbols[0]["location"]["range"]
                start_line_idx = def_loc_range["start"]["line"]
                start_col_idx = def_loc_range["start"]["character"]
                end_line_idx = def_loc_range["end"]["line"]
                end_col_idx = def_loc_range["end"]["character"]
                def_lo_idx = _get_index_from_line_col(
                    text=text,
                    line=start_line_idx,  # 0-based
                    col=start_col_idx,  # 0-based
                )
                try:
                    def_hi_idx = _get_index_from_line_col(
                        text=text,
                        line=end_line_idx,  # 0-based
                        col=end_col_idx,  # 0-based
                    )
                except AssertionError:
                    # ugly but enough, I don't have more time ...
                    def_hi_idx = len(text)
                assert start_line_idx <= symbol_line_idx <= end_line_idx
                definition = text[def_lo_idx:def_hi_idx]
                definition_lines = definition.split("\n")
                definition_lines[
                    symbol_line_idx - start_line_idx
                ] += f" // <<<<< {file_path_to_show}:{symbol_line_idx + 1}, definition of `{symbol_name}`"
                definition = "\n".join(definition_lines)
                # for idx, line in enumerate(definition_lines, start=def_lo_idx):

                del def_loc_range
                del start_col_idx, end_col_idx
                del def_lo_idx, def_hi_idx

                pl_type = _detect_PL_from_file_suffix(file_path_to_show) or ""

                return {
                    "lang": pl_type,
                    "file_path": file_path_to_show,
                    "start_line": start_line_idx + 1,
                    "end_line": end_line_idx + 1,
                    "definition": definition,
                }

            def _make_multiple_definition_response(locations: list) -> str:
                if not locations:
                    return "No definition found."

                result_sb = StringIO()
                for i, d in enumerate(locations, start=1):
                    r = _make_single_definition_response(d)
                    result_sb.write(
                        f"### Definition {i}. {r['file_path']}:{r['start_line']}-{r['end_line']}\n"
                    )
                    result_sb.write(f"```{r['lang']}\n")
                    result_sb.write(r["definition"].rstrip() + "\n")
                    result_sb.write(f"```\n")
                return result_sb.getvalue()

            def _make_multi_references_response(ds: list) -> str:

                def _make_single_r(index: int, d: dict) -> str:
                    abs_file_path = d["absolutePath"]
                    rel_file_path = d["relativePath"]
                    file_path_to_show = f"{ctx.repo_name}/{rel_file_path}"

                    text = instance.read_file(abs_file_path)
                    if text is None:
                        self.dlog.w(f"File not exists: {abs_file_path}")
                        return f"{index}. Error: Found a reference to this symbol in {file_path_to_show}, but failed to read the file."

                    lo_idx = _get_index_from_line_col(
                        text=text,
                        line=d["range"]["start"]["line"],
                        col=d["range"]["start"]["character"],
                    )
                    hi_idx = _get_index_from_line_col(
                        text=text,
                        line=d["range"]["end"]["line"],
                        col=d["range"]["end"]["character"],
                    )

                    symbol_name = text[lo_idx:hi_idx]
                    text_lines = text.split("\n")
                    center_line_idx = d["range"]["start"]["line"]  # 0-based
                    start_line_idx = max(center_line_idx - 3, 0)
                    end_line_idx = min(center_line_idx + 3, len(text_lines))
                    ref_ctx_lines = text_lines[start_line_idx:end_line_idx]
                    ref_ctx_lines[
                        center_line_idx - start_line_idx
                    ] += f" // <<<<< {file_path_to_show}:{center_line_idx + 1}, reference of `{symbol_name}`"
                    ref_ctx_ctt = "\n".join(ref_ctx_lines)

                    self.dlog(f"References{[index]} in {abs_file_path}")
                    # self.dlog(f">>>> location: {json.dumps(d, indent=2)}")
                    # self.dlog(f">>>> symbol_name: '{symbol_name}'")
                    # self.dlog(f">>>> ref_ctx_ctt: \n```{ref_ctx_ctt}```")

                    lang = _detect_PL_from_file_suffix(file_path_to_show) or ""

                    return f"""\
### Reference {index}. {file_path_to_show}:{start_line_idx + 1}-{end_line_idx + 1}
```{lang}
{ref_ctx_ctt}
```
"""

                if not ds:
                    return "No reference found."

                result_sb = StringIO()
                for index, d in enumerate(ds):
                    result_sb.write(_make_single_r(index, d))
                    result_sb.write("\n")
                return result_sb.getvalue()

            _make_result = {
                FIND_DEFINITION.lower(): _make_multiple_definition_response,
                FIND_REFERENCES.lower(): _make_multi_references_response,
            }
            show_markers = _show_code_symbol_markers(code_symbol_markers)
            result_sb = StringIO()
            for i, (marker, lsp_resp) in enumerate(
                zip(code_symbol_markers, lsp_responses, strict=True)
            ):
                shown_marker = show_markers[i]
                fn_i, rel_fn, line, op, start_col, end_col = marker
                file_ctt_lines = old_contents[fn_i].split("\n")
                marking_line = new_contents[fn_i].split("\n")[line - 1]
                symbol_line = file_ctt_lines[line - 1]
                symbol = symbol_line[start_col - 1 : end_col - 1]

                if lsp_resp["type"] == "error":
                    _msg = lsp_resp["message"]
                    if "Unsupported encoding" in _msg:
                        _msg += "\nThis tool is usable for this file; use other tools instead."
                    return f"Error: {_msg}"

                assert lsp_resp["type"] != "error"
                operation = lsp_resp["type"]
                assert operation.upper() == op

                self.dlog(f"Making result for {i} ...")
                self.dlog(f">>>> symbol_name: '{symbol}'")
                self.dlog(f">>>> symbol_line: '{symbol_line}'")
                self.dlog(f">>>> marking_line: '{marking_line}'")

                result_sb.write(f"# queries[{i}]. {operation.upper()}({symbol})\n")
                result_sb.write(f"```\n")
                result_sb.write(f"{shown_marker.rstrip()}\n")
                result_sb.write(f"```\n")
                result_sb.write("\n")
                result_sb.write(f"## Results\n")
                ### Definition or Reference ...
                result_sb.write(_make_result[operation](lsp_resp["result"]))
                result_sb.write("\n")
            return result_sb.getvalue()

        return (
            _show_lsp_responses(lsp_responses).strip()
            or f"Not found `{FIND_DEFINITION}` or `{FIND_REFERENCES}`"
        )

    return [_resolve_code_symbol]


@contextlib.contextmanager
def _start_lsp_server(instance, ctx):
    if not ctx.enable_code_symbol_analysis_toolkit:
        yield
        return

    class _FakeSelf:
        def __init__(self):
            self.dlog = _dlog

    self = _FakeSelf()

    setup_cmd = f"bash {instance.config.REPO_TOOLKIT_DIR}/setup_lsp_server.sh"
    self.dlog(f"Setup LSP server environment ...")
    instance.communicate(
        setup_cmd,
        timeout=None,
        check="raise",
        error_msg=f"Failed to setup LSP server environment: {setup_cmd}",
    )
    del setup_cmd

    server_cmd = f"bash {instance.config.REPO_TOOLKIT_DIR}/lsp_server.sh --repo_dir {instance.repo_path}"
    self.dlog(f"Start LSP server ...")
    instance.communicate(
        server_cmd,
        timeout=None,
        check="raise",
        error_msg=f"Failed to start LSP server: {server_cmd}",
    )
    del server_cmd

    # some cases' compilation may take a long time because of network (git submodule ...)
    TIMEOUT_SECONDS, PERIOD_SECONDS = (60 * 60, 20)
    for _ in range(TIMEOUT_SECONDS // PERIOD_SECONDS):
        ckh_cmd = (
            f"bash {instance.config.REPO_TOOLKIT_DIR}/request_lsp.sh --operation health"
        )
        health_resp = json.loads(
            instance.communicate(
                ckh_cmd,
                timeout=None,
                check="raise",
                error_msg=f"Failed to check LSP server health: {ckh_cmd}",
                quiet=True,
            )
        )
        if health_resp["type"] == "health" and health_resp["result"] == "running":
            self.dlog(f"LSP server ready.")
            break
        self.dlog.w(f"LSP server not ready, wait & retry ...")
        time.sleep(PERIOD_SECONDS)
    else:
        raise TimeoutError(f"Bad State: LSP not ready after {TIMEOUT_SECONDS}s")

    yield

    self.dlog(f"Exit LSP server ...")
    exit_cmd = (
        f"bash {instance.config.REPO_TOOLKIT_DIR}/request_lsp.sh --operation exit"
    )
    nop_cmd = "sleep 1 && echo 'NOP'"  # eat `[1]+  Done ...`
    exit_resp = json.loads(
        instance.communicate(
            exit_cmd,
            timeout=None,
            check="raise",
            error_msg=f"Failed to exit LSP server: {exit_cmd}",
        )
    )
    if exit_resp["type"] == "exit" and exit_resp["result"] == "exited":
        self.dlog(f"LSP server exited.")
    else:
        self.dlog.w(f"Failed to exit LSP server.")
        self.dlog.w(f">>>> type: {exit_resp['type']}")
        self.dlog.w(f">>>> message: {exit_resp.get('message')}")
        self.dlog.w(f">>>> result: {exit_resp.get('result')}")
    instance.communicate(
        nop_cmd,
        timeout=None,
        check="raise",
        error_msg=f"Failed to eat `[1]+  Done ...`: {nop_cmd}",
    )


def _make_enahanced_report(
    instance: Instance,
    tool_name: str,
    submitted_json: List[dict],
    submitted_text: List[str],
) -> str:
    return {
        "clang_static_analyzer": _make_enahanced_report_for_clang_static_analyzer,
        "facebook_infer": _make_enahanced_report_for_facebook_infer,
        "cppcheck": _make_enahanced_report_for_cppcheck,
    }[tool_name](instance, submitted_json, submitted_text)


class SimpleEditLocAgent(Agent):
    @dataclass
    class Input:
        instance: Instance

    @dataclass
    class Output:
        # instance info
        instance_info: Dict[str, Any]

        # 1. Locate suspicious files
        ## 1.1 Locate suspicious files with prompting
        identified_files: List[str]
        ## 1.2 Locate suspicious files with retrieving
        irrelevant_folders: List[str]
        remaining_files: List[str]
        built_vector_store: Dict[str, Any]  # (Deprecated)
        retrieved_docs: List[Dict[str, Any]]
        retrieved_files: List[str]
        ## 1.3 Merge suspicious files from 1.1 and 1.2
        suspicious_files: List[str]
        # 2. Locate suspicious elements
        suspicious_file_infos: Dict[str, Any]
        suspicious_elements: List[Dict[str, Any]]

        issue_descriptions: Dict[str, Any]

        llm_query_records: List[LLMQueryRecord] = None
        asked_agent_outputs: List[AgentAskRecord] = None

    def __init__(
        self,
        model: Model,
        embed_model_config: Dict[str, Any],
        max_loc_files_with_prompting: int,
        max_loc_files_with_retrieving: int,
        chunk_size_when_retrieving: int,
        chunk_overlap_when_retrieving: int,
        # For w/ context pre-collection
        enable_context_pre_collection: bool = False,
        # For w/ safety property generation
        enable_safety_property_analysis: bool = False,
        **kwargs,
    ):
        super().__init__("SimpleELA", **kwargs)
        self.__model = model
        self.__score_model = model
        self.__embed_model_config = embed_model_config
        self.__max_loc_files_with_prompting = max_loc_files_with_prompting
        self.__max_loc_files_with_retrieving = max_loc_files_with_retrieving
        self.__chunk_size_when_retrieving = chunk_size_when_retrieving
        self.__chunk_overlap_when_retrieving = chunk_overlap_when_retrieving
        self.__enable_context_pre_collection = enable_context_pre_collection
        self.__enable_safety_property_analysis = enable_safety_property_analysis

    def _locate_suspicious_files(
        self,
        instance: Instance,
        issue_description: str,
    ) -> Dict[str, Any]:
        # Parse repo structure
        repo_structure_info = instance.get_repo_structure()
        repo_structure_description = repo_structure_info["description"]
        repo_files = repo_structure_info["files"]

        ## 1.1 Locate suspicious files with prompting
        self.ilog(f"1.1 Identifying suspicious files with prompting ...")
        loc_file_messages = []
        loc_file_prompt = OBTAIN_SUSPICIOUS_FILES_PROMPT.format(
            issue_description=issue_description,
            repo_structure=repo_structure_description,
            max_files=self.__max_loc_files_with_prompting,
            repo_name=instance.repo_path.split("/")[-1],
        )
        loc_file_messages.append(Message.system(DEFAULT_SYSTEM_PROMPT))
        loc_file_messages.append(Message.user(loc_file_prompt))
        T = float(os.getenv("FLOC1_LLM_TEMPERATURE", "0.0"))
        if T != 0.0:
            self.dlog.w(f">>>> FLOC1_LLM_TEMPERATURE: {T}")
        loc_file_response = self.__model.ask(loc_file_messages, temperature=T)
        del T
        self.dlog(f">>>> LLM Response: `{loc_file_response.content}`")
        try:
            identified_files = _parse_code_block(loc_file_response.content).split("\n")
        except Exception as ex:
            resp_content = loc_file_response.content
            resp_content_lines = resp_content.strip().split("\n")
            _1st_line, _1st_idx = None, None
            for i, line in enumerate(resp_content_lines):
                if line.strip().startswith("```"):
                    _1st_idx = i
                    _1st_line = line
                    break
            else:
                raise ex
            assert _1st_line is not None
            assert _1st_line.strip().startswith("```")
            assert _1st_line == resp_content_lines[_1st_idx]
            resp_content = "\n".join(resp_content_lines[_1st_idx:])
            del resp_content_lines, _1st_idx
            _L = "plaintext"
            self.dlog.w(f"Failed to parse code block without lang, {ex}")
            if _1st_line.startswith(f"```{_L}"):
                self.dlog.w(f">>>> retry ```{_L} ...")
                identified_f_block = _parse_code_block(resp_content, _L)
                identified_files = identified_f_block.split("\n")
            elif _1st_line.startswith("```"):
                self.dlog.w(f">>>> retry fixing the first line ...")
                resp_content = "```\n" + resp_content.removeprefix("```").lstrip()
                identified_f_block = _parse_code_block(resp_content)
                identified_files = identified_f_block.split("\n")
            else:
                raise ex
            del _L, identified_f_block
        identified_files += ["/".join(f.split("/")[1:]) for f in identified_files]
        identified_file_count = len(identified_files)
        self.ilog(f"Identified suspicious files: {identified_files}")
        identified_files = [
            f.strip() for f in identified_files if f.strip() in repo_files
        ]
        if len(identified_files) < identified_file_count:
            self.dlog.w(f"len(files after filter) < len(identified suspicious files)")
            self.dlog.w(f">>>> len(initial suspicious files): {identified_file_count}")
            self.dlog.w(f">>>> len(filtered suspicious files): {len(identified_files)}")
        identified_files = identified_files[: self.__max_loc_files_with_prompting]
        del loc_file_messages
        del loc_file_prompt
        del loc_file_response
        del identified_file_count

        ## 1.2 Locate suspicious files with retrieving
        self.ilog(f"1.2 Identifying suspicious files with retrieving ...")

        ### 1.2.1 Filter out irrelevant folders
        self.ilog(f"1.2.1 Identifying irrelevant folders with prompting ...")
        filter_irr_messages = []
        filter_irr_prompt = OBTAIN_IRRELEVANT_FILES_PROMPT.format(
            issue_description=issue_description,
            repo_structure=repo_structure_description,
            repo_name=instance.repo_path.split("/")[-1],
        )
        filter_irr_messages.append(Message.system(DEFAULT_SYSTEM_PROMPT))
        filter_irr_messages.append(Message.user(filter_irr_prompt))
        T = float(os.getenv("FLOC2_LLM_TEMPERATURE", "0.0"))
        if T != 0.0:
            self.dlog.w(f">>>> FLOC2_LLM_TEMPERATURE: {T}")
        filter_irr_response = self.__model.ask(filter_irr_messages, temperature=T)
        del T
        self.dlog(f">>>> LLM Response: `{filter_irr_response.content}`")
        try:
            irr_folders = _parse_code_block(filter_irr_response.content).split("\n")
        except Exception as ex:
            _L = "plaintext"
            self.dlog.w(f"Failed to parse code block without lang, retry ```{_L} ...")
            identified_f_block = _parse_code_block(filter_irr_response.content, _L)
            irr_folders = identified_f_block.split("\n")
            del _L, identified_f_block
        irr_folders += ["/".join(f.strip().split("/")[1:]) for f in irr_folders]
        self.ilog(f"Identified irrelevant folders: {irr_folders}")
        self.ilog(f"Filtering out irrelevant files")
        self.ilog(f">>>> before: {len(repo_files)}")
        remaining_files = [
            file
            for file in repo_files
            if not any(file.startswith(folder) for folder in irr_folders)
        ]
        self.ilog(f">>>> after: {len(remaining_files)}")
        del filter_irr_messages
        del filter_irr_prompt
        del filter_irr_response

        ### 1.2.2 Retrieve files related to issue description
        self.ilog(f"1.2.2 Retrieving files related to issue description ...")
        docs_of_rem_files = []
        for file in remaining_files:
            docs_of_rem_files.append(
                {
                    "path": file,
                    "text": instance.read_file(f"{instance.repo_path}/{file}"),
                }
            )
        self.dlog(f"Building vector store for {instance.id}...")
        self.dlog(f">>>> len(files): {len(docs_of_rem_files)}")
        vector_store = SimpleVectorStore(
            embed_model_config=self.__embed_model_config.copy(),
            documents=docs_of_rem_files,
            chunk_size=self.__chunk_size_when_retrieving,
            chunk_overlap=self.__chunk_overlap_when_retrieving,
        )
        retri_K = self.__max_loc_files_with_retrieving * 100
        self.dlog(f"Retrieving {retri_K} docs")
        retrieved_docs = vector_store.search(issue_description, k=retri_K)
        self.dlog(f">>>> len(retrieved docs): {len(retrieved_docs)}")
        retrieved_files = []
        for doc in retrieved_docs:  # Get top k files
            if doc.metadata["path"] not in retrieved_files:
                retrieved_files.append(doc.metadata["path"])
        self.dlog(f"Len(retrieved files (ALL)): {len(retrieved_files)}")
        retrieved_files = retrieved_files[: self.__max_loc_files_with_retrieving]
        assert len(retrieved_files) <= self.__max_loc_files_with_retrieving
        if len(retrieved_files) < self.__max_loc_files_with_retrieving:
            self.dlog.w(f"Len(retrieved files) < max_loc_files_with_retrieving")
            self.dlog.w(f">>>> len(retrieved files): {len(retrieved_files)}")
            self.dlog.w(f">>>> max_l._f._w/_r.: {self.__max_loc_files_with_retrieving}")
        self.ilog(f"Retrieved suspicious files: {retrieved_files}")
        del docs_of_rem_files
        del retri_K

        ## 1.3 Merge suspicious files from 1.1 and 1.2
        assert len(identified_files) <= self.__max_loc_files_with_prompting
        assert len(retrieved_files) <= self.__max_loc_files_with_retrieving
        self.ilog("1.3 Merging suspicious files from prompting and retrieval ...")
        self.dlog(f">>>> identified suspicious files: {identified_files}")
        self.dlog(f">>>> retrieved suspicious files: {retrieved_files}")
        merge_file_counter = Counter(identified_files)
        for file in identified_files:
            merge_file_counter[file] += 1
        for file in retrieved_files:
            merge_file_counter[file] += 1
        suspicious_files = [f for f, _ in merge_file_counter.most_common()]
        del merge_file_counter
        self.ilog(f"Located suspicious files: {suspicious_files}")
        self.dlog(f">>>> len(identified files): {len(identified_files)}")
        self.dlog(f">>>> len(retrieved files): {len(retrieved_files)}")
        self.dlog(f">>>> len(suspicious files): {len(suspicious_files)}")

        with open(
            f"{self._output_dir}/vector-store-record-for-locating-suspicious-files-with-retrieving.json",
            mode="w",
        ) as fp:
            json.dump(vector_store.get_record(), fp)

        return {
            "identified_files": identified_files,
            "irrelevant_folders": irr_folders,
            "remaining_files": remaining_files,
            # "built_vector_store": vector_store.get_record(),  # may be big
            "built_vector_store": {"note": "deprecated, saved as a single file"},
            "retrieved_docs": [d.model_dump() for d in retrieved_docs],
            "retrieved_files": retrieved_files,
            "suspicious_files": suspicious_files,
        }

    def _locate_suspicious_elements(
        self,
        instance: Instance,
        issue_description: str,
        file_level_loc_result: Dict[str, Any],
        suspicious_file_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        ## [{'element_type': ..., 'element_path': ...}, ...]

        def _fix_response(response: str) -> str:
            response_lines = response.split("\n")
            for line_i in range(len(response_lines)):
                line = response_lines[line_i]
                sline = line.strip()
                leading_spaces = line[: len(line) - len(line.lstrip())]
                if (
                    sline.startswith("{")
                    or sline.startswith("}")
                    or sline.startswith("]")
                    or sline.startswith("[")
                ):
                    continue
                if (
                    sline.endswith("{")
                    or sline.endswith("}")
                    or sline.endswith("]")
                    or sline.endswith("[")
                ):
                    continue
                try:
                    line_obj = eval("{" + sline + "}", {}, {})
                    if isinstance(line_obj, set):
                        if "::" in sline:
                            sline = f'"element_path": {sline}'
                        else:
                            sline = f'"element_type": {sline}'
                    response_lines[line_i] = leading_spaces + sline
                except Exception as ex:
                    self.dlog.w(f"Failed to fix line ({line_i}): {line}")
                    self.dlog.w(f">>>> traceback: `{_get_exception_tb(ex)}`")
                    continue
                del leading_spaces, sline, line, line_i, line_obj
            return "\n".join(response_lines)

        TRY_GEN_JSON = 3

        suspicious_files = file_level_loc_result["suspicious_files"]
        file_info = suspicious_file_info

        file_skeletons_sb = StringIO()
        for i, (_, file) in enumerate(file_info.items()):
            if file["path"] not in suspicious_files:
                continue
            file_skeletons_sb.write(f">>>> File {i}. {file['path']} <<<<\n")
            file_skeletons_sb.write("```")
            file_skeletons_sb.write(file["skeleton"])
            file_skeletons_sb.write("```")
            file_skeletons_sb.write(f">>>> END OF FILE {i} <<<<\n")
            file_skeletons_sb.write("\n")
        file_skeletons = file_skeletons_sb.getvalue()

        element_level_loc_messages = []
        element_level_loc_prompt = OBTAIN_SUSPICIOUS_ELEMENTS_PROMPT.format(
            issue_description=issue_description,
            file_skeletons=file_skeletons,
        )
        element_level_loc_messages.append(Message.system(DEFAULT_SYSTEM_PROMPT))
        element_level_loc_messages.append(Message.user(element_level_loc_prompt))
        for gj_i in range(TRY_GEN_JSON):
            ##################### Generate JSON Response ######################
            try:
                self.dlog("Requesting LLM to locate suspicious elements ...")
                T = float(os.getenv("ELTLOC_LLM_TEMPERATURE", "0.0"))
                if T != 0.0:
                    self.dlog.w(f">>>> ELTLOC_LLM_TEMPERATURE: {T}")
                element_level_loc_response = self.__model.ask(
                    messages=element_level_loc_messages,
                    temperature=T,
                    response_format={"type": "json_object"},
                )
                del T
                self.dlog(f">>>> LLM Response: `{element_level_loc_response.content}`")
                element_level_loc_result = element_level_loc_response.content
                element_level_loc_result = _fix_response(element_level_loc_result)
                element_level_loc_result = json.loads(element_level_loc_result.strip())
            except json.JSONDecodeError as ex:
                self.dlog.w(f"Failed to parse response ({ex})")
                self.dlog.w(f">>>> response: `{element_level_loc_response.content}`")
                self.dlog.w(f">>>> traceback: `{_get_exception_tb(ex)}`")
                self.dlog.w(f"Tried {gj_i} times to gen JSON, retrying ...")
                continue  # retry gen JSON

            ##################### Parse JSON Response ######################
            try:
                assert isinstance(element_level_loc_result, dict)
                assert "elements" in element_level_loc_result
                element_level_loc_result = element_level_loc_result["elements"]
                assert isinstance(element_level_loc_result, list)
                element_level_loc_result, ori_ellr = [], element_level_loc_result
                for item in ori_ellr:
                    if "element_type" not in item:
                        if "type" in item:
                            item["element_type"] = item.pop("type")
                    if "element_path" not in item:
                        if "path" in item:
                            item["element_path"] = item.pop("path")
                    assert "element_type" in item
                    assert "element_path" in item
                    # assert item["element_type"].strip() in [ "class", "struct", "union", "enum", "function", "macro", "global_variable"]
                    if item["element_type"] not in [
                        "class",
                        "struct",
                        "union",
                        "enum",
                        "function",
                        "macro",
                        "global_variable",
                    ]:
                        continue  # skip invalid element
                    assert "::" in item["element_path"]
                    file, rel_path = item["element_path"].strip().split("::", 1)
                    if file not in suspicious_files:
                        file = file.removeprefix(f"{instance.repo_name}/")
                        item["element_path"] = f"{file}::{rel_path}"
                    if file not in suspicious_files:
                        self.dlog.w(f"File {file} not in suspicious files")
                        continue  # skip invalid element
                    # assert file in suspicious_files # DON'T ASSERT
                    # assert file in file_info # DON'T ASSERT
                    existing_elements = file_info[file]["element_paths"]  # rel paths
                    # assert rel_path in existing_elements # DON'T ASSERT
                    # assert item["element_path"] in file_info[file]["abs_element_paths"] # DON'T ASSERT

                    # Add the element to the result
                    element_level_loc_result.append(item)
                break  # get a valid JSON response
            except AssertionError as ex:
                self.dlog.w(f"Got invalid JSON response ({ex})")
                self.dlog.w(f">>>> suspicious files: {suspicious_files}")
                self.dlog.w(f">>>> suspicious elements: {element_level_loc_result}")
                try:
                    self.dlog.w(f">>>>>| bad item: {item}")
                    self.dlog.w(f">>>>>| elements: {existing_elements}")
                except:
                    pass
                continue  # retry gen JSON
        else:
            self.flog(
                f"Failed to gen valid JSON response after {TRY_GEN_JSON} times",
                exp_cls=RuntimeError,
            )

        for item in element_level_loc_result:
            item["type"] = item["element_type"].strip()
            item["file"], item["path"] = item["element_path"].strip().split("::", 1)
            item["abs_path"] = item["element_path"].strip()
            del item["element_type"], item["element_path"]
        self.ilog(f"Located {len(element_level_loc_result)} suspicious elements")
        for element in element_level_loc_result:
            self.ilog(f">>>> {element['abs_path']} ({element['type']})")

        return {
            "suspicious_file_infos": file_info,
            "suspicious_elements": element_level_loc_result,
        }

    def _get_raw_poc_output(self, instance: Instance) -> str:
        if cached_poc_output := self._load_cache(key="poc_output"):
            return cached_poc_output

        build_r = instance.build()
        assert build_r.success
        repro_r = instance.repro()
        assert repro_r.sanitizer_triggered
        poc_output = repro_r.raw_output
        del build_r, repro_r

        self._save_cache(poc_output, key="poc_output")
        return poc_output

    def _get_poc_output(self, instance: Instance) -> str:
        return self._get_raw_poc_output(instance)

    def _get_poc_output_v2(self, instance: Instance) -> str:
        raise NotImplementedError("Unhelpful")

        USE_EXTENDED_POC_OUTPUT = os.getenv("ENABLE_SEARCH_CODE_TOOLKIT_V2", "0") == "1"

        poc_output = self._get_raw_poc_output(instance)
        if not USE_EXTENDED_POC_OUTPUT:
            return poc_output

        call_stack_fns: str | None = self._extract_stack_info_from_poc_output(
            instance=instance, log=poc_output
        )
        if not call_stack_fns:
            return poc_output

        # self.dlog(f"Functions in call stack: \n=======\n{call_stack_fns}\n=======\n")
        return f"""\
{poc_output}

In addition, the location information of the functions in the call stack(s) show as follows:
{call_stack_fns}
You can read the source code of the functions by using the `read_code_in_file` tool.
"""

    def _extract_stack_info_from_poc_output(self, instance: Instance, log: str) -> str:
        if cached_call_stack_fns := self._load_cache(key="call_stack_functions"):
            return cached_call_stack_fns["text"]

        self.dlog("Extracting call stack functions from sanitizer log ...")

        sanitizer_log = _extract_sanitizer_report(log)
        stack_frames = _extract_stack_frames_from_sanitizer_log(sanitizer_log)
        file_info_cache = {}

        def _search_function_in_file(abs_file_path: str, lineno: int, name: str):
            assert os.path.isabs(abs_file_path)
            if not abs_file_path.startswith(instance.repo_path):
                return None
            if instance.read_file(abs_file_path) is None:
                return None

            file_path = abs_file_path.removeprefix(instance.repo_path).removeprefix("/")
            if file_path not in file_info_cache:
                info = instance.get_code_structure(files=[file_path])
                file_info_cache.update(info)
            file_info = file_info_cache[file_path]

            def _search(elts: list | None):
                results = []
                for elt in elts or []:
                    if elt["name"] == name:
                        results.append(elt)
                    if "children" in elt:
                        results.extend(_search(elt["children"]))
                return results

            results = _search(file_info["elements"])
            results = [
                elt
                for elt in results
                if elt["type"] == "function"
                and elt["range"]["start_line"] <= lineno <= elt["range"]["end_line"]
                and not not elt["body_range"]
            ]

            if not results:
                return None

            return results

        def _make_item_description(file_path: str, function: str, elt: dict) -> str:
            start_line = elt["range"]["start_line"]
            end_line = elt["range"]["end_line"]
            return f'`{function}` ( {{ "file_path": "{file_path}" , "start_line": {start_line} , "end_line": {end_line} }} )'

        elts: List[dict] = []
        item_lines: List[str] = []
        for frame_idx, frame in enumerate(stack_frames):
            file = frame["file"]
            function = frame["function"]
            lineno = frame["line"]
            results = _search_function_in_file(file, lineno, function) or []
            self.dlog(f">>>> Processed ALL_Frames[{frame_idx}]: `{frame}`")
            self.dlog(f">>>>>> Found {[r['abs_path'] for r in results or []]}")
            elts.extend(results)
            for elt in results:
                item_str = _make_item_description(file, function, elt)
                if item_str not in item_lines:
                    item_lines.append(f" - {item_str}")
                del elt
            del frame_idx, frame
        text = "\n".join(item_lines) if item_lines else None

        self._save_cache(
            key="call_stack_functions",
            data={
                "text": text,
                "elts": elts,
                "item_lines": item_lines,
                "sanitizer_log": sanitizer_log,
                "original_log": log,
            },
        )
        return text

    def _pre_collect_context(self, instance: Instance, issue_description: str) -> str:
        from langchain.agents import AgentExecutor, create_tool_calling_agent, tool
        from langchain_core.prompts import ChatPromptTemplate

        if cached_result := self._load_cache(key="pre_collected_context"):
            return cached_result

        MAX_TOOL_CALLS = int(
            os.getenv("PRE_COLLECT_CONTEXT_MAX_TOOL_CALLS", "<NO_DEFAULT>")
        )
        ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_FOR_PRECC = bool(
            os.getenv("ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_FOR_PRECC", "0")
        )

        @dataclass
        class _PreCC_ToolCallCtx(_ToolCallCtx):
            repo_name: str = None
            enable_code_symbol_analysis_toolkit: bool = False
            enable_code_symbol_analysis_toolkit_v2: bool = False
            enable_code_symbol_analysis_toolkit_v3: bool = False

        ctx = _PreCC_ToolCallCtx(
            repo_name=instance.repo_name,
            max_calls=MAX_TOOL_CALLS,
            message_when_max_calls_reached="Tool call limit reached. Stop invoking tools and generate the final analysis based on the context collected so far.",
            enable_code_symbol_analysis_toolkit=ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_FOR_PRECC,
            enable_code_symbol_analysis_toolkit_v2=ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_FOR_PRECC,
            enable_code_symbol_analysis_toolkit_v3=ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_FOR_PRECC,
        )
        repo_structure_info = instance.get_repo_structure()
        repo_structure_description = repo_structure_info["description"]
        poc_output = self._get_poc_output(instance)

        search_code_toolkit = _make_search_code_toolkit(instance, ctx)
        sym_ana_toolkit = _make_code_sysmbol_analysis_toolkit(instance, ctx)

        if not ctx.enable_code_symbol_analysis_toolkit:
            assert not sym_ana_toolkit

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", DEFAULT_SYSTEM_PROMPT),
                ("human", "{prompt}"),
                ("placeholder", "{agent_scratchpad}"),
            ]
        )
        tools = [*search_code_toolkit, *sym_ana_toolkit]
        tool_names = [tool.name for tool in tools]
        agent = create_tool_calling_agent(
            self.__model.as_langchain_model(),
            tools,
            prompt,
        )
        agent_executor = AgentExecutor(
            agent=agent,
            tools=tools,
            stream_runnable=False,
            verbose=True,
            max_iterations=MAX_TOOL_CALLS + 5,
            handle_parsing_errors=True,
        )

        if any(M in self.__model.name for M in ["o3-mini", "gpt-4o"]):
            self.dlog("Using OpenAI models-specific prompt template.")
            prompt_tmpl = PRE_COLLECT_CONTEXT_PROMPT_FOR_OPENAI_MODELS
        else:
            self.dlog("Using default prompt template.")
            self.dlog(">>>> verified on DeepSeek-V3.2-Exp")
            prompt_tmpl = PRE_COLLECT_CONTEXT_PROMPT

        prompt = prompt_tmpl.format(
            tool_names=tool_names,
            issue_description=issue_description,
            poc_output=poc_output,
            repo_structure=repo_structure_description,
            max_iterations=MAX_TOOL_CALLS,
        )
        self.dlog(f"Pre-collect context prompt: \n==========\n{prompt}\n==========\n")
        self.dlog(f"Pre-collect context tools: {tool_names}")
        with (
            _start_lsp_server(instance=instance, ctx=ctx) as lsp_server,
            LLMQueryRecorder(propagate_to_parent=True) as recorder,
        ):
            r = agent_executor.invoke(
                {"prompt": prompt},
                config={
                    "callbacks": [
                        _create_on_agent_action_callback(ctx),
                        _create_on_tool_X_callback(ctx),
                    ]
                },
            )
        # self.dlog(f"Agent output: \n==========\n{r['output']}\n==========\n")
        self._save_cache(
            r["output"],
            key="pre_collected_context",
            llm_query_records=recorder.records,
            agent_ctx=ctx,
        )
        return r["output"]  # DON'T PARSE CODE BLOCK HERE!!!!!!!!!!!!!!

    def _perform_safety_property_analysis(
        self,
        instance: Instance,
        issue_description: str,
    ) -> dict:
        from langchain.agents import AgentExecutor, create_tool_calling_agent, tool
        from langchain_core.prompts import ChatPromptTemplate
        from llm_sandbox import SandboxSession
        from llm_sandbox.exceptions import SandboxTimeoutError

        @dataclass
        class _SingleEdit:
            file: str
            search: str
            replace: str
            _raw: str

        @dataclass
        class _SingleFileEdit:
            file: str
            edits: List[_SingleEdit]
            original_file_content: str = None
            patched_file_content: str = None

        @dataclass
        class _Edit:
            index: int  # unique
            name: str  # unique
            commit_hash: str

            # file(rel path, not start with '/reponame') => edit
            edits: Dict[str, _SingleFileEdit]
            actual_edited_files: List[str]

        @dataclass
        class _PocOutput:
            index: int  # unique
            name: str  # unique
            commit_hash: str  # == applied_edits[-1].commit_hash
            build_result: BuildResult
            repro_result: Optional[ReproResult]
            output: str
            applied_edits: List[_Edit]  # old --> new

        @dataclass
        class _SPA_ToolCallCtx(_ToolCallCtx):

            # Configurations
            repo_name: str = None
            max_poc_output_chars: int = None
            max_python_output_chars: int = None
            max_python_timeout: int = None  # seconds
            python_session: Any | None = None
            run_python_tool_name: str = None
            enable_code_symbol_analysis_toolkit: bool = False
            enable_code_symbol_analysis_toolkit_v2: bool = False
            enable_code_symbol_analysis_toolkit_v3: bool = False
            tool_use_guidance: str = None

            # States
            applied_edits: List[_Edit] = None  # old --> new
            all_edits: List[_Edit] = None  # old --> new
            poc_outputs: List[_PocOutput] = None  # old --> new
            base_commit_hash: str = None

            def __post_init__(self):
                self.all_edits = []
                self.applied_edits = []
                self.poc_outputs = []

                git_rev_parse_cmds = [
                    f"pushd {instance.repo_path} 1>/dev/null 2>/dev/null",
                    "git rev-parse HEAD",
                    "popd 1>/dev/null 2>/dev/null",
                ]
                self.base_commit_hash = instance.communicate(
                    " && ".join(git_rev_parse_cmds),
                    timeout=None,
                    check="raise",
                    error_msg=f"Failed to get HEAD commit hash after rolling back all {len(self.applied_edits)} applied edits",
                ).strip()
                _dlog(f"Get base commit: {self.base_commit_hash}")
                assert self.base_commit_hash == instance.config.base_commit

        def _make_poc_execution_toolkit(
            instance: Instance,
            ctx: _SPA_ToolCallCtx,
        ) -> list:

            @tool(
                "run_poc",
                description=f"""\
Compiles the current project (with your current applied edits) and executes the Proof of Concept (PoC) test.

This tool performs a full compilation of the project and runs the PoC.
The execution output is automatically saved and can be retrieved later using get_poc_output() in Python code interpreter tool (i.e., `{ctx.run_python_tool_name}`).

Args:
    name (str): A meaningful, unique identifier for this PoC run. Used to retrieve the full output later.
                If the name conflicts with existing PoC outputs, it will be automatically modified.
                The corrected unique identifier will be returned in <name></name> tags.

Returns:
    str: A truncated view (last {ctx.max_poc_output_chars} characters) of the PoC execution output.
            The complete output is preserved and can be accessed using `get_poc_output(name: str)` in `{ctx.run_python_tool_name}`
            with the name provided in the <name></name> tags.

Note:
    - The truncated output shows only the last {ctx.max_poc_output_chars} characters for immediate review
    - The full output is saved and can be retrieved programmatically using `get_poc_output(name: str)` in `{ctx.run_python_tool_name}`
    - Use descriptive names to easily identify different test runs (e.g., "assertion_for_p_pointer", "assertion_for_potential_overflow_index")
    - Name conflicts are automatically resolved with unique identifiers

Example usage:
    run_poc("assertion_for_p_pointer_dereference")
    # Later retrieve full output in Python code interpreter: get_poc_output("assertion_for_p_pointer_dereference")
""",
            )
            def _run_poc(name: str) -> str:
                if ctx.num_calls > ctx.max_calls:
                    return f"Error: {ctx.message_when_max_calls_reached}"

                all_poc_names = [poc_output.name for poc_output in ctx.poc_outputs]
                for suffix in ["", *list(range(1000))]:
                    new_name = f"{name}{suffix}"
                    if new_name not in all_poc_names:
                        unique_name = new_name
                        break
                else:
                    self.elog("bad state, no unique name for poc output")
                    exit(-1)  # bad state, let it crash

                hint = ""
                if name != unique_name:
                    hint = f"Your provided name '{name}' conflicts with existing PoC outputs. It is corrected to '{unique_name}'."
                hint = f"\n<hint>{hint}</hint>\n" if hint else ""

                build_r = instance.build()  # secb build
                repro_r = None
                if build_r.success:
                    repro_r = instance.repro()  # secb repro

                if not build_r.success:
                    if not build_r.timeout:
                        status = "Build project with your applied edits failed."
                        output_tag = "build_failure_log"
                    else:
                        status = "Build project with your applied edits timed out."
                        output_tag = "build_timeout_log"
                    original_output = build_r.raw_output
                else:
                    assert repro_r is not None
                    if not repro_r.timeout:
                        status = "The project was compiled and the PoC was run to completion. The run output has been saved."
                        output_tag = "poc_output"
                    else:
                        status = "Compile successfully, but run PoC timed out."
                        output_tag = "run_poc_timeout_log"
                    original_output = repro_r.raw_output
                truncated_output = original_output[-ctx.max_poc_output_chars :]
                applied_edit_names = [e.name for e in ctx.applied_edits]

                # Update ctx's states
                ctx.poc_outputs.append(
                    _PocOutput(
                        index=len(ctx.poc_outputs),
                        name=unique_name,
                        commit_hash=[
                            ctx.base_commit_hash,
                            *[e.commit_hash for e in ctx.applied_edits],
                        ][-1],
                        build_result=build_r,
                        repro_result=repro_r,
                        output=original_output,
                        applied_edits=ctx.applied_edits.copy(),  # COPY is necessary
                    )
                )
                assert len(ctx.poc_outputs) == len(all_poc_names) + 1
                assert len([p for p in ctx.poc_outputs if p.name == unique_name]) == 1

                if output_tag == "poc_output":
                    original_output_lines = original_output.split("\n")
                    pass_assertion_lines = [
                        L.strip()
                        for L in original_output_lines
                        if L.strip().startswith("[PASS]")
                    ]
                    fail_assertion_lines = [
                        L.strip()
                        for L in original_output_lines
                        if L.strip().startswith("[FAIL]")
                    ]
                    num_pass_assertions = len(pass_assertion_lines)
                    num_fail_assertions = len(fail_assertion_lines)
                    num_all_assertions = num_pass_assertions + num_fail_assertions

                    truncated_fail_assertions = fail_assertion_lines[-50:]
                    truncated_fail_assertions = [
                        f"        - `{L}`" for L in truncated_fail_assertions
                    ]

                    if num_fail_assertions > 0:
                        fail_assertion_show = f"""\
    <fail_assertions total={num_fail_assertions} num_shown={len(truncated_fail_assertions)} num_discarded={num_fail_assertions - len(truncated_fail_assertions)}>
        [FAIL] assertions:
        (... only show the last {len(truncated_fail_assertions)} fail assertion lines ...)
{'\n'.join(truncated_fail_assertions)}
    </fail_assertions>
"""
                    else:
                        assert num_fail_assertions == 0
                        fail_assertion_show = ""

                    if num_all_assertions > 0:
                        poc_output_stats = f"""\
<poc_output_stats>
    <the_number_of_assertions>
        #All assertions: {num_all_assertions}
        #[FAIL] assertions: {num_fail_assertions}
        #[PASS] assertions: {num_pass_assertions}
    </the_number_of_assertions>

{fail_assertion_show}

Hint: To do more analysis, you can leverage the `{ctx.run_python_tool_name}` tool.

</poc_output_stats>"""
                    else:
                        assert num_fail_assertions == 0
                        assert num_pass_assertions == 0
                        assert num_all_assertions == 0
                        poc_output_stats = ""
                else:
                    poc_output_stats = ""

                return f"""\
<name>{unique_name}</name>{hint}

<status>{status}</status>

<num_applied_edits>{len(applied_edit_names)}</num_applied_edits>
<applied_edits_from_old_to_new>{applied_edit_names}</applied_edits_from_old_to_new>
<current_latest_applied_edit>{applied_edit_names[-1] if applied_edit_names else 'No applied edit'}</current_latest_applied_edit>

{poc_output_stats}

<output total_chars={len(original_output)} num_shown_chars={len(truncated_output)} num_truncated_chars={len(original_output) - len(truncated_output)}>{truncated_output}</output>
"""

            return [_run_poc]

        def _make_codebase_editing_toolkit(
            instance: Instance,
            ctx: _SPA_ToolCallCtx,
        ) -> list:

            @tool(
                "apply_edits",
                description=f"""\
Applies the provided *SEARCH/REPLACE* edits to the repository and tracks the changes with a unique identifier.

This tool applies your code edits to the project files. Each edit must follow the specified format and can modify one location in one file. The edits are processed by grouping them by file and applying all changes to each file in sequence.

Args:
    name (str): A meaningful identifier for this set of edits. Used to track and reference the changes.
               If the name conflicts with existing edits, it will be automatically modified and returned to ensure uniqueness.
    search_replace_edits (List[str]): A list of strings, where each string is a complete *SEARCH/REPLACE* edit block following the exact format requirements.

Returns:
    str: A detailed report including:
         - Corrected edit name (auto-modified if the provided name conflicts with existing ones)
         - Edit validity (format, file existence checks)
         - Application success status
         - Number of content changes made
         - List of modified file paths
         - Detailed application status for each edit (i.e., whether it changed the content)
         - Total count of all successfully applied edits (this one and previous ones)
         - Current list of all successfully applied edit names (this one and previous ones)
         - Name of the latest edit (if this application was successful, it will be the name of this edit; otherwise, it will be the name of the previous edit)

Processing Logic:
    for filename, edits_in_a_file in parse_and_group_edit_by_file(search_replace_edits):
        old_content: str = read_file_from_repo(filename)
        new_content: str = old_content
        for single_edit in edits_in_a_file:
            search: str = single_edit.search
            replace: str = single_edit.replace
            new_content: str = new_content.replace(search, replace)
        write_file_to_repo(filename, new_content, mode='w')

Note:
    - Provide a meaningful, unique name to identify this set of edits
    - Conflicting names are automatically corrected
    - All successfully applied edits are tracked and visible when running PoC, rolling back edits, or parsing a selected PoC output
    - Each edit block must target exactly one file and one code location
    - Multiple edits can be applied to the same file or different files

Every *SEARCH/REPLACE* edit must use this format:
1. The file path
2. The start of search block: <<<<<<< SEARCH
3. A contiguous chunk of lines to search for in the existing source code
4. The dividing line: =======
5. The lines to replace into the source code
6. The end of the replace block: >>>>>>> REPLACE

Here is an example:

<edit_example>
### {ctx.repo_name}/path/to/example.c
<<<<<<< SEARCH
int b = *ptr * 2;
=======
int b = 0;
if (ptr != NULL) {{
    b = *ptr * 2;
}}
>>>>>>> REPLACE
</edit_example>

Please note that the *SEARCH/REPLACE* edit REQUIRES PROPER INDENTATION. If you would like to add the line '        printf(x)', you must fully write that out, with all those spaces before the code!
Please note that you must provide sufficient *SEARCH* edit context (No less than 3 lines of code) to ensure that the code location can be successfully searched!
Please note that you can't use "..." or any other to alter and ignore the original code content, you must keep the original code format and content in the *SEARCH/REPLACE* edit!
Please note that your MUST provide the file path at the first line of each *SEARCH/REPLACE* edit with the format: '### {ctx.repo_name}/path/to/a/file.c'
Please note: The file path your provide must point to a C/C++ file in the repository (starting from the repo name "{ctx.repo_name}", e.g., {ctx.repo_name}/folder1/file1.{{c,h,cpp,cc,cxx,C,hpp,hh,hxx,H}})
Note that if multiple *SEARCH/REPLACE* edits are needed, provide multiple *SEARCH/REPLACE* edits as a list of strings (i.e., search_replace_edits: List[str])""",
            )
            def _apply_edits(
                name: str, search_replace_edits: Union[str, List[str]]
            ) -> str:
                if isinstance(search_replace_edits, str):
                    search_replace_edits = [search_replace_edits]

                if ctx.num_calls > ctx.max_calls:
                    return f"Error: {ctx.message_when_max_calls_reached}"

                def _make_applied_edits_history(applied_edits) -> str:
                    applied_edit_names = [e.name for e in applied_edits]
                    return f"""\
<num_applied_edits>{len(applied_edit_names)}</num_applied_edits>
<applied_edits_from_old_to_new>{applied_edit_names}</applied_edits_from_old_to_new>
<current_latest_applied_edit>{applied_edit_names[-1] if applied_edit_names else "No applied edit"}</current_latest_applied_edit>"""

                error_applied_his = _make_applied_edits_history(ctx.applied_edits)

                self.dlog("\n\n".join(["Edits: ```", *search_replace_edits, "```"]))

                # Check name
                all_edit_names = [edit.name for edit in ctx.all_edits]
                for suffix in ["", *list(range(1000))]:
                    new_name = f"{name}{suffix}"
                    if new_name not in all_edit_names:
                        unique_name = new_name
                        break
                else:
                    self.elog("bad state, no unique name for edit")
                    exit(-1)  # bad state, let it crash
                hint = ""
                if name != unique_name:
                    hint = f"Your provided name '{name}' conflicts with existing ones. It is corrected to '{unique_name}'."
                hint = f"\n<hint>{hint}</hint>\n" if hint else ""

                # Check edit format
                self.dlog("Checking edits format ...")
                try:
                    # sr_patches = _parse_search_replace_patch(search_replace_edits)
                    sr_patches = _parse_search_replace_patch(
                        search_replace_edits, remove_line_marker=True
                    )
                except ValueError as ex:
                    return (
                        f"Error: Apply edits failed; the provided search-replace edits are invalid. Details: {ex}\n"
                        + error_applied_his
                    )

                # Check files to be edited
                self.dlog("Checking to-edit files ...")
                old_contents = []
                file_rel_paths = []
                for srp in sr_patches:
                    if not srp.file.startswith(f"{ctx.repo_name}/"):
                        return (
                            f"Error: Apply edits failed; the file path '{srp.file}' does not start with the repo name '{ctx.repo_name}'.\n"
                            + error_applied_his
                        )
                    rel_file_path = srp.file.removeprefix(f"{ctx.repo_name}/")
                    file_rel_paths.append(rel_file_path)
                    abs_file_path = os.path.join(instance.repo_path, rel_file_path)
                    content = instance.read_file(abs_file_path)
                    if content is None:
                        srp.file = f"{ctx.repo_name}/{srp.file}"  # add a name
                        rel_file_path = srp.file.removeprefix(f"{ctx.repo_name}/")
                        abs_file_path = os.path.join(instance.repo_path, rel_file_path)
                        content = instance.read_file(abs_file_path)
                        file_rel_paths.pop(-1)
                        file_rel_paths.append(rel_file_path)
                    if content is None:
                        return (
                            f"Error: Apply edits failed; the file path '{srp.file}' does not exist.\n"
                            + error_applied_his
                        )
                    old_contents.append(content)
                assert len(file_rel_paths) == len(set(file_rel_paths))

                # Apply edits

                def _whitespace_insensitive_replace(text, old, new):
                    import re

                    parts = re.split(r"\s+", old.strip())
                    pattern = r"\s+".join(re.escape(part) for part in parts)

                    regex = re.compile(pattern)
                    return regex.sub(new, text)

                ## Calcu new content for each file
                self.dlog("Calculating new content for each file ...")
                new_contents = []
                edit_details = {}  # index => details
                file_edit_details = {}  # filename => details
                for ctt, sr_patch in zip(old_contents, sr_patches, strict=True):
                    assert ctt is not None
                    patched_ctt = ctt
                    for diff in sr_patch.patches:
                        index: int = diff.index
                        patched_ctt_1 = patched_ctt.replace(diff.search, diff.replace)
                        edit_details[index] = {"edited": (patched_ctt != patched_ctt_1)}
                        patched_ctt = patched_ctt_1
                    if ctt == patched_ctt:
                        self.dlog.w("Bad patch; Try whitespace-insensitive replace")
                        for diff in sr_patch.patches:
                            index: int = diff.index
                            patched_ctt_1 = _whitespace_insensitive_replace(
                                patched_ctt, diff.search, diff.replace
                            )
                            edit_details[index] = {
                                "edited": (patched_ctt_1 != patched_ctt)
                            }
                            patched_ctt = patched_ctt_1
                        if ctt == patched_ctt:
                            self.dlog.w("Still bad patch, keep going")
                    file_edit_details[sr_patch.file] = {"edited": ctt != patched_ctt}
                    new_contents.append(patched_ctt)
                assert len(file_edit_details) == len(old_contents) == len(sr_patches)
                assert len(edit_details) == sum([len(p.patches) for p in sr_patches])

                ## Check if any edit is applied
                self.dlog("Checking if any edit is applied ...")
                any_edited = any(v["edited"] for v in edit_details.values())
                any_file_edited = any(v["edited"] for v in file_edit_details.values())
                if not any_edited:
                    assert not any_file_edited
                    return (
                        f"Error: Apply edits failed; no content change detected after trying to apply the edits. Details: {edit_details}\n"
                        + error_applied_his
                    )

                ## Apply edits to repo
                self.dlog("Applying validated edits to repo ...")
                instance.apply_patch_without_auto_restore(
                    Patch(
                        patch=search_replace_edits,
                        file_paths=file_rel_paths,
                        original_file_contents=old_contents,
                        patched_file_contents=new_contents,
                    )
                )

                ## Construct git commit for the edits
                self.dlog("Git commit applyied edits ...")
                commit_msg = f"Apply edits for {unique_name}"
                git_commmit_cmds = [
                    f"pushd {instance.repo_path}",
                    f"git add {' '.join(file_rel_paths)}",
                    f'git commit -m "{commit_msg}"',
                    f"popd",
                ]

                if instance.config.id in ["libsndfile.cve-2018-19432"]:
                    # Not good practice but enough
                    self.dlog.w(f"To-Remove git hooks for {instance.config.id}")
                    _rm_ghook = f"rm -rf {instance.repo_path}/.git/hooks/pre-commit"
                    git_commmit_cmds[1:1] = [_rm_ghook]
                    del _rm_ghook

                instance.communicate(
                    " && ".join(git_commmit_cmds),
                    timeout=None,
                    check="raise",
                    error_msg="Failed to apply edits to repo",
                )

                ## Get HEAD commit hash
                git_rev_parse_cmds = [
                    f"pushd {instance.repo_path} 1>/dev/null 2>/dev/null",
                    "git rev-parse HEAD",
                    "popd 1>/dev/null 2>/dev/null",
                ]
                commit_hash_after_apply = instance.communicate(
                    " && ".join(git_rev_parse_cmds),
                    timeout=None,
                    check="raise",
                    error_msg="Failed to get HEAD commit hash after applying edits",
                ).strip()

                # Update ctx's states
                ctx.all_edits.append(
                    _Edit(
                        index=len(ctx.all_edits),
                        name=unique_name,
                        commit_hash=commit_hash_after_apply,
                        edits={
                            f: _SingleFileEdit(
                                file=f,
                                edits=[
                                    _SingleEdit(
                                        file=f,
                                        search=diff.search,
                                        replace=diff.replace,
                                        _raw=diff.raw,
                                    )
                                    for diff in srp.patches
                                ],
                                original_file_content=old_ctt,
                                patched_file_content=new_ctt,
                            )
                            for f, srp, old_ctt, new_ctt in zip(
                                file_rel_paths,
                                sr_patches,
                                old_contents,
                                new_contents,
                                strict=True,
                            )
                        },
                        actual_edited_files=[
                            f for f, d in file_edit_details.items() if d["edited"]
                        ],
                    )
                )
                assert len(ctx.all_edits) >= 1
                ctx.applied_edits.append(ctx.all_edits[-1])
                assert len(ctx.applied_edits) >= 1
                assert ctx.applied_edits[-1].index == len(ctx.all_edits) - 1
                assert len(ctx.all_edits) == len(all_edit_names) + 1
                assert len(ctx.applied_edits) <= len(ctx.all_edits)

                # Returns:
                #     str: A detailed report including:
                #          - Corrected edit name (auto-modified if the provided name conflicts with existing ones)
                #          - Edit validity (format, file existence checks)
                #          - Application success status
                #          - Number of content changes made
                #          - List of modified file paths
                #          - Detailed application status for each edit (i.e., whether it changed the content)
                #          - Total count of all successfully applied edits
                #          - Current list of all successfully applied edit names
                #          - Name of the latest edit (if this application was successful, it will be the name of this edit; otherwise, it will be the name of the previous edit)

                num_changes = sum(v["edited"] for v in edit_details.values())
                num_file_changed = sum(v["edited"] for v in file_edit_details.values())
                edit_details_sb = StringIO()
                for index, details in edit_details.items():
                    status = (
                        "Edited (old_content != new_content)"
                        if details["edited"]
                        else "Not Edited (old_content == new_content)"
                    )
                    edit_details_sb.write(f"search_replace_edits[{index}]: {status}\n")
                edit_details = edit_details_sb.getvalue()
                ok_applied_his = _make_applied_edits_history(ctx.applied_edits)

                result = f"""\
<name>
    <unique_name>{unique_name}</unique_name>{hint}
</name>

<status>Applied {unique_name} successfully.</status>

<num_changes>{num_changes}</num_changes>
<num_file_changed>{num_file_changed}</num_file_changed>
<edit_details>
{edit_details}
</edit_details>
<edited_files>
{[f for f, d in file_edit_details.items() if d["edited"]]}
</edited_files>

{ok_applied_his}
"""
                # self.dlog(f"Result: \n=====\n{result}\n=====\n")
                return result

            @tool(
                "rollback_the_latest_one_edit",
                description=f"""\
Rolls back the most recently you previously applied edit from the repository.

This tool reverses the latest set of changes that were applied using apply_edits. It removes the most recent edit from the tracking system and restores the affected files to their state before that changes were applied.

Returns:
    str: A detailed report including:
         - Name of the rolled back edit
         - Rollback success status
         - List of files that were restored to their previous state
         - Updated list of remaining applied edit names
         - Total count of remaining applied edits
         - Name of the new latest edit (if any edits remain)

Note:
    - Only one edit can be rolled back at a time (LIFO - Last In First Out)
    - The rollback operation is performed in reverse order of application
    - After rollback, the removed edit is no longer tracked and cannot be retrieved
    - If no edits are currently applied, the operation will fail with an appropriate message
    - This operation affects both the file contents and the edit tracking system

Example usage flow:
    1. apply_edits("add_assertion_1", edits_list1) -> Latest: "add_assertion"
    2. apply_edits("add_assertion_2", edits_list2) -> Latest: "add_assertion_2"
    3. rollback_the_latest_one_edit() -> Rolls back "add_assertion_2", Latest: "add_assertion_1"

Use this tool when you want to undo the last applied edit and restore the repository to its previous state.
""",
            )
            def _rollback_the_latest_one_edit() -> str:

                def _make_applied_edits_history(applied_edits) -> str:
                    applied_edit_names = [e.name for e in applied_edits]
                    return f"""\
<num_applied_edits>{len(applied_edit_names)}</num_applied_edits>
<applied_edits_from_old_to_new>{applied_edit_names}</applied_edits_from_old_to_new>
<current_latest_applied_edit>{applied_edit_names[-1] if applied_edit_names else "No applied edit"}</current_latest_applied_edit>"""

                if ctx.num_calls > ctx.max_calls:
                    return f"Error: {ctx.message_when_max_calls_reached}"

                if not ctx.applied_edits:
                    return (
                        "Error: No applied edit to rollback.\n"
                        + _make_applied_edits_history(ctx.applied_edits)
                    )

                # Update states & Git reset
                assert len(ctx.applied_edits) >= 1
                head_applied_edit: _Edit = ctx.applied_edits.pop()
                target_commit_hash = [
                    ctx.base_commit_hash,
                    *[e.commit_hash for e in ctx.applied_edits],
                ][-1]
                git_reset_cmds = [
                    f"pushd {instance.repo_path}",
                    "git reset --hard HEAD~1",
                    "popd",
                ]
                instance.communicate(
                    " && ".join(git_reset_cmds),
                    timeout=None,
                    check="raise",
                    error_msg=f"Failed to rollback the latest edit `{head_applied_edit.name}` by `{' && '.join(git_reset_cmds)}`",
                )

                ## Get HEAD commit hash
                git_rev_parse_cmds = [
                    f"pushd {instance.repo_path} 1>/dev/null 2>/dev/null",
                    "git rev-parse HEAD",
                    "popd 1>/dev/null 2>/dev/null",
                ]
                commit_hash_after_rollback = instance.communicate(
                    " && ".join(git_rev_parse_cmds),
                    timeout=None,
                    check="raise",
                    error_msg=f"Failed to get HEAD commit hash after rolling back the latest edit `{head_applied_edit.name}` by `{' && '.join(git_reset_cmds)}`",
                ).strip()
                assert commit_hash_after_rollback == target_commit_hash

                return f"""\
<name_of_rolled_back_edit>{head_applied_edit.name}</name_of_rolled_back_edit>
<status>Rolled back {head_applied_edit.name} successfully.</status>

<files_restored_to_previous_state>{head_applied_edit.actual_edited_files}</files_restored_to_previous_state>

{_make_applied_edits_history(ctx.applied_edits)}
"""

            @tool(
                "rollback_all_applied_edits",
                description=f"""\
Rolls back all applied edits from the repository and clears the edit tracking history.

This tool completely reverses all changes that were applied using apply_edits. It removes all tracked edits and restores all modified files to their original state before any edits were applied.

Returns:
    str: A detailed report including:
         - List of all rolled back edit names
         - Total number of edits removed
         - List of all files that were restored to their original state

Note:
    - This operation rolls back ALL applied edits, not just the latest one
    - All file modifications from previous edits will be reverted
    - The edit tracking system will be completely cleared
    - This is a destructive operation and cannot be undone
    - If no edits are currently applied, the operation will report error with a message.

Example usage flow:
    1. apply_edits("add_assertion_1", edits_list1) -> Latest: "add_assertion_1"
    2. apply_edits("add_assertion_2", edits_list2) -> Latest: "add_assertion_2"
    3. apply_edits("add_assertion_3", edits_list3) -> Latest: "add_assertion_3"
    4. rollback_all_applied_edits() -> Rolls back all three edits, clears history

Use this tool when you want to start fresh or when multiple edits need to be completely removed.
""",
            )
            def _rollback_all_applied_edits() -> str:
                def _make_applied_edits_history(applied_edits) -> str:
                    applied_edit_names = [e.name for e in applied_edits]
                    return f"""\
<num_applied_edits>{len(applied_edit_names)}</num_applied_edits>
<applied_edits_from_old_to_new>{applied_edit_names}</applied_edits_from_old_to_new>
<current_latest_applied_edit>{applied_edit_names[-1] if applied_edit_names else "No applied edit"}</current_latest_applied_edit>"""

                if ctx.num_calls > ctx.max_calls:
                    return f"Error: {ctx.message_when_max_calls_reached}"

                if not ctx.applied_edits:
                    return (
                        "Error: No applied edit to rollback.\n"
                        + _make_applied_edits_history(ctx.applied_edits)
                    )

                # Update states & Git reset
                assert len(ctx.applied_edits) >= 1
                rollbacked_edits = ctx.applied_edits.copy()
                ctx.applied_edits = []  # Clear applied edits
                target_commit_hash = [
                    ctx.base_commit_hash,
                    *[e.commit_hash for e in ctx.applied_edits],
                ][-1]
                assert target_commit_hash == ctx.base_commit_hash
                git_reset_cmds = [
                    f"pushd {instance.repo_path}",
                    f"git reset --hard HEAD~{len(rollbacked_edits)}",
                    "popd",
                ]
                instance.communicate(
                    " && ".join(git_reset_cmds),
                    timeout=None,
                    check="raise",
                    error_msg=f"Failed to rollback all {len(rollbacked_edits)} applied edits by `{' && '.join(git_reset_cmds)}`",
                )

                ## Get HEAD commit hash
                git_rev_parse_cmds = [
                    f"pushd {instance.repo_path} 1>/dev/null 2>/dev/null",
                    "git rev-parse HEAD",
                    "popd 1>/dev/null 2>/dev/null",
                ]
                commit_hash_after_rollback = instance.communicate(
                    " && ".join(git_rev_parse_cmds),
                    timeout=None,
                    check="raise",
                    error_msg=f"Failed to get HEAD commit hash after rolling back all {len(ctx.applied_edits)} applied edits",
                ).strip()
                assert (
                    commit_hash_after_rollback
                    == target_commit_hash
                    == ctx.base_commit_hash
                )

                return f"""\
<names_of_rolled_back_edit>{[e.name for e in rollbacked_edits]}</names_of_rolled_back_edit>
<status>Rolled back all {len(rollbacked_edits)} applied edits successfully.</status>

<files_restored_to_previous_state>{sorted(set(f for e in rollbacked_edits for f in e.actual_edited_files))}</files_restored_to_previous_state>

{_make_applied_edits_history(ctx.applied_edits)}
"""

            return [
                _apply_edits,
                _rollback_the_latest_one_edit,
                _rollback_all_applied_edits,
            ]

        def _make_python_code_execution_toolkit(
            instance: Instance,
            ctx: _SPA_ToolCallCtx,
        ) -> list:

            @tool(
                ctx.run_python_tool_name,
                description=f"""\
A Python code interpreter. With this tool, you can execute Python code to help analyze the output of the poc output, which may be so long.

In the interpreter environment, a set of global functions are already defined to help you retrieve the poc outputs:
- `get_poc_output(name: str) -> str`: Get the output of the poc with the given name.

Requirements for using this tool:
- The input must be valid Python code (Python version: 3.11).
- To produce output, you must print it using `print(...)`.
- Only standard libraries can be used for data processing and analysis. You should import them first before using them in the code.
- Do not attempt to access the file system (e.g., reading/writing files) or the network. If you need to read/search code, use the search code toolkit (e.g., `search_code_element_in_file`, `read_code_in_file`) (DO NOT CALL THEM IN PYTHON CODE).
- The stdout and stderr of each Python execution will be automatically truncated, keeping only the first {ctx.max_python_output_chars} characters.

Guidelines for using this tool to analyze the output of the static analysis tool:
- Remember: the stdout and stderr of each Python code execution is automatically truncated to the first {ctx.max_python_output_chars} characters. So once again — **do not print all output at once; query and print only the important parts!**
- You can use various methods to query and analyze the outputs:
    - You can use Python string operations, regular expressions or any others to query and analyze the poc output.
    - If you are not sure how to query the output, try to print the leading part of the results (e.g., the first 20 lines) to get a sense of the structure. Then, based on the structure, you can write more precise analysis code.
    - To ensure you know how many results you have, first print the count of results you want to print, then print the results. This helps you detect truncation issues. If truncation occurs, refine your queries for more precise results.
- You also can use this tool to do other analysis, such as doing some calculation for pointer address, index (big/no-normal) integer, etc.
- In short, Python code interpreter is a flexible tool — leverage its expressive power to perform precise&effective analysis based on poc output.
    - But any attempt to access the file system (e.g., reading/writing files, listing directories) and network access is forbidden.
    - Any attempt to run commands (e.g., `os.system`, `subprocess.*`) is forbidden.
    - The Python interpreter environment is sandboxed, which is not working with the repository you are analyzing. So you cannot find any files in the repository.
    - You can only use this tool to do the basic and pure computation operations, such as string operations, regular expressions, and basic arithmetic operations.

Args:
    code (str): The Python code to execute.
Returns:
    The output of the Python code execution, including the exit code, stdout, and stderr.
""",
            )
            def _run_python_code(code: str) -> str:
                def _make_applied_edits_history(applied_edits) -> str:
                    applied_edit_names = [e.name for e in applied_edits]
                    return f"""\
<num_applied_edits>{len(applied_edit_names)}</num_applied_edits>
<applied_edits_from_old_to_new>{applied_edit_names}</applied_edits_from_old_to_new>
<current_latest_applied_edit>{applied_edit_names[-1] if applied_edit_names else "No applied edit"}</current_latest_applied_edit>"""

                if ctx.num_calls > ctx.max_calls:
                    return f"Error: {ctx.message_when_max_calls_reached}"

                output_filepath_prefix = f"/sandbox/{instance.config.id}"
                poc_outputs_jf = f"{output_filepath_prefix}_poc_outputs.json"

                code_prefix = f"""\
def _MAKE_get_poc_output():
    import json
    with open("{poc_outputs_jf}", "r") as fp:
        loaded = json.load(fp)
    def get_poc_output(name: str) -> str:
        if name not in loaded:
            raise ValueError(f"Poc output with name {{name}} not found.")
        return loaded[name]['output']
    return get_poc_output

get_poc_output = _MAKE_get_poc_output()
del _MAKE_get_poc_output

class NotSupportedError(Exception):
    pass

def _fake_open(*args, **kwargs):
    raise NotSupportedError("Reading/writing files is not supported in the python environment.")
globals()["open"] = _fake_open
del _fake_open

def _fake_access_filesystem(*args, **kwargs):
    raise NotSupportedError("Accessing filesystem is not supported in the python environment.")

import glob
glob.glob = _fake_access_filesystem

import os
def _fake_system(*args, **kwargs):
    raise NotSupportedError("Executing commands is not supported in the python environment.")
os.system = _fake_system
del _fake_system

import os
os.listdir = _fake_access_filesystem

import subprocess
def _fake_subprocess_call(*args, **kwargs):
    raise NotSupportedError("Executing commands is not supported in the python environment.")
subprocess.Popen = _fake_subprocess_call
subprocess.run = _fake_subprocess_call
subprocess.call = _fake_subprocess_call
subprocess.check_call = _fake_subprocess_call
subprocess.check_output = _fake_subprocess_call
subprocess.getstatusoutput = _fake_subprocess_call
subprocess.getoutput = _fake_subprocess_call
del _fake_subprocess_call

import re
import json
"""

                self.dlog(
                    f"Run python code: \n#########################################\n"
                    + code
                    + "\n#########################################\n"
                )

                assert ctx.python_session is not None

                with _make_temp_file_mgr(n=1, auto_delete=True) as tf_mgr:
                    self.dlog("Copying poc outputs to python session ...")
                    _pO_tmp_fn = tf_mgr.files[0]
                    with open(_pO_tmp_fn, "w") as fp:
                        json.dump({po.name: asdict(po) for po in ctx.poc_outputs}, fp)
                    ctx.python_session.copy_to_runtime(_pO_tmp_fn, poc_outputs_jf)

                full_code = f"{code_prefix}\n\n{code}"
                try:
                    self.dlog("Running python code ...")
                    result = ctx.python_session.run(
                        full_code, timeout=ctx.max_python_timeout
                    )
                    exit_code = result.exit_code
                    original_stdout = result.stdout
                    original_stderr = result.stderr
                except SandboxTimeoutError:
                    self.dlog.w(
                        f"Python code execution timed out after {ctx.max_python_timeout}s"
                    )
                    exit_code = "TIMEOUT"
                    original_stdout = ""
                    original_stderr = f"Error: Your code execution timed out after {ctx.max_python_timeout} seconds. You should optimize your code to run faster."

                truncated_stdout = original_stdout[: ctx.max_python_output_chars]
                truncated_stderr = original_stderr[: ctx.max_python_output_chars]

                result = f"""\
<exit_code>{exit_code}</exit_code>

<stdout total_chars={len(original_stdout)} num_shown_chars={len(truncated_stdout)} num_truncated_chars={len(original_stdout) - len(truncated_stdout)}>{truncated_stdout}</stdout>

<stderr total_chars={len(original_stderr)} num_shown_chars={len(truncated_stderr)} num_truncated_chars={len(original_stderr) - len(truncated_stderr)}>{truncated_stderr}</stderr>

{_make_applied_edits_history(ctx.applied_edits)}"""
                # self.dlog(
                #     f"Python code execution result: \n#########################################\n"
                #     + result
                #     + "\n#########################################\n"
                # )

                return result

            return [_run_python_code]

        def _make_toolkit_for_spa(instance: Instance, ctx: _SPA_ToolCallCtx) -> list:
            # Tools for SafetyPropertyAnalysisAgent
            ## 1. Search Code Toolkit
            ## 2. Code Symbol Analysis Toolkit
            ## 3. Codebase Editing Toolkit
            ## 4. PoC Execution Toolkit
            ## 5. Python Code Execution Toolkit

            return [
                *_make_code_sysmbol_analysis_toolkit(instance, ctx),
                *_make_search_code_toolkit(instance, ctx),
                *_make_codebase_editing_toolkit(instance, ctx),
                *_make_poc_execution_toolkit(instance, ctx),
                *_make_python_code_execution_toolkit(instance, ctx),
            ]

        ###############################################################################

        if cached_result := self._load_cache(key="safety_property_analysis_result"):
            return cached_result

        # fmt: off
        MAX_TOOL_CALLS = int(os.getenv("SAFETY_PROPERTY_ANALYSIS_MAX_TOOL_CALLS", "<NO_DEFAULT>"))
        MAX_RUN_POC_OUTPUT_CHARS = int(os.getenv("MAX_RUN_POC_OUTPUT_CHARS", "<NO_DEFAULT>"))
        MAX_PYTHON_INTERPRETER_OUTPUT_CHARS = int(os.getenv("MAX_PYTHON_INTERPRETER_OUTPUT_CHARS", "<NO_DEFAULT>"))
        MAX_PYTHON_INTERPRETER_TIMEOUT_SECONDS = int(os.getenv("MAX_PYTHON_INTERPRETER_TIMEOUT_SECONDS", "<NO_DEFAULT>"))
        RUN_PYTHON_TOOL_NAME = "run_python_code"
        # ENABLE_SEARCH_CODE_TOOLKIT_V2 = os.getenv("ENABLE_SEARCH_CODE_TOOLKIT_V2", "0") == "1" # unhelpful
        ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT = os.getenv("ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT", "0") == "1"
        ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_V2 = os.getenv("ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_V2", "0") == "1"
        ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_V3 = os.getenv("ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_V3", "0") == "1"
        # fmt: on

        tool_use_guidance = None
        if ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_V2:
            tool_use_guidance = """\
 - resolve_code_symbol: to find the definition or references of a code symbol (e.g., a function, a type/struct/class, a variable, etc.)
 - read_code_in_file: to read some lines of code in a file
 - apply_edits: to modify the code in the codebase, e.g., inserting safety property assertions.
 - rollback_the_latest_one_edit: to rollback the latest edit set applied by `apply_edits`
 - rollback_all_applied_edits: to rollback all edit sets applied by `apply_edits`
 - run_poc: to run the PoC of this vulnerability
 - run_python_code: to run a Python code; but cannot be used to read/write files, execute commands, or access network.
"""

        ctx = _SPA_ToolCallCtx(
            repo_name=instance.repo_name,
            max_calls=MAX_TOOL_CALLS,
            message_when_max_calls_reached="Tool call limit reached. Stop invoking tools and generate the analysis report based on the attempted analyses so far.",
            max_poc_output_chars=MAX_RUN_POC_OUTPUT_CHARS,
            max_python_output_chars=MAX_PYTHON_INTERPRETER_OUTPUT_CHARS,
            max_python_timeout=MAX_PYTHON_INTERPRETER_TIMEOUT_SECONDS,
            run_python_tool_name=RUN_PYTHON_TOOL_NAME,
            enable_code_symbol_analysis_toolkit=ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT,
            enable_code_symbol_analysis_toolkit_v2=ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_V2,
            enable_code_symbol_analysis_toolkit_v3=ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_V3,
            tool_use_guidance=tool_use_guidance,
        )
        repo_structure_info = instance.get_repo_structure()
        repo_structure_description = repo_structure_info["description"]
        # poc_output = self._get_poc_output(instance)

        spa_toolkit = _make_toolkit_for_spa(instance, ctx)
        if (
            ctx.enable_code_symbol_analysis_toolkit_v2
            and not ctx.enable_code_symbol_analysis_toolkit_v3
        ):
            spa_toolkit = [
                sap_tool_
                for sap_tool_ in spa_toolkit
                if sap_tool_.name != "search_code_element_in_file"
            ]

        agent_tools = [*spa_toolkit]
        agent_tool_names = [tool.name for tool in agent_tools]

        agent_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", DEFAULT_SYSTEM_PROMPT),
                ("human", "{prompt}"),
                ("placeholder", "{agent_scratchpad}"),
            ]
        )
        agent = create_tool_calling_agent(
            self.__model.as_langchain_model(),
            agent_tools,
            agent_prompt,
        )
        agent_executor = AgentExecutor(
            agent=agent,
            tools=agent_tools,
            stream_runnable=False,
            verbose=True,
            max_iterations=MAX_TOOL_CALLS + 5,
            handle_parsing_errors=True,
        )

        if any(M in self.__model.name for M in ["o3-mini", "gpt-4o"]):
            self.dlog("Using OpenAI models-specific prompt template.")
            prompt_tmpl = SAFETY_PROPERTY_ANALYSIS_PROMPT_FOR_OPENAI_MODELS
            ext_kwargs = {}
        else:
            self.dlog("Using default prompt template.")
            self.dlog(">>>> verified on DeepSeek-V3.2-Exp")
            prompt_tmpl = SAFETY_PROPERTY_ANALYSIS_PROMPT
            ext_kwargs = {"max_iterations": MAX_TOOL_CALLS}

        prompt = prompt_tmpl.format(
            tool_names=agent_tool_names,
            tool_use_guidance=ctx.tool_use_guidance or "",
            issue_description=issue_description,
            repo_structure=repo_structure_description,
            **ext_kwargs,
        )
        self.dlog(f"Safety property analysis prompt: \n====\n{prompt}\n====\n")
        self.dlog()
        self.dlog(f"Using {len(spa_toolkit)} tools")
        for t in spa_toolkit:
            self.dlog(f">>>> `{t.name}`")

        with (
            SandboxSession(lang="python", keep_template=True, verbose=True) as session,
            _start_lsp_server(instance=instance, ctx=ctx) as lsp_server,
        ):
            ctx.python_session = session
            with LLMQueryRecorder(propagate_to_parent=True) as recorder:

                if os.getenv("DEBUG_TOOL_USEAGE") == "1":
                    tool_map = {t.name: t for t in agent_tools}
                    self.dlog(f"Tools: {tool_map.keys()}")
                    while True:
                        cmd = input("> Cmd: ")
                        if cmd == "exit":
                            exit(0)
                        elif cmd == "continue":
                            break
                        elif cmd.startswith("call"):
                            try:
                                _, name, kwargs = cmd.split(" ", maxsplit=2)
                                kwargs = json.loads(kwargs.strip())
                                ret = tool_map[name].run(kwargs)
                                self.dlog(f"Tool {name} return: `{ret}`")
                            except Exception as e:
                                self.dlog.w(f"Failed to call tool {name}: {e}")
                    del tool_map

                r = agent_executor.invoke(
                    input={"prompt": prompt},
                    config={
                        "callbacks": [
                            _create_on_agent_action_callback(ctx),
                            _create_on_tool_X_callback(ctx),
                        ]
                    },
                )

            # Get git diff
            if ctx.applied_edits:
                self.dlog(f"Construct git diff ...")
                base_commit = ctx.base_commit_hash
                head_commit = ctx.applied_edits[-1].commit_hash
                diff_file = f"/{_get_uuid()}_spa_assertions.diff"
                git_diff_cmds = [  # do NOT read diff from stdout (--no-pager does not work)
                    f"pushd {instance.repo_path} 1>/dev/null 2>/dev/null",
                    f"git diff --no-color {base_commit} {head_commit} > {diff_file}",
                    "popd 1>/dev/null 2>/dev/null",
                ]
                instance.communicate(
                    " && ".join(git_diff_cmds),
                    timeout=None,
                    check="raise",
                    error_msg=f"Failed to get git diff between {base_commit} and {head_commit}",
                )
                git_diff = instance.read_file(diff_file)
                self.dlog(f"Git diff: \n====\n{git_diff}\n====\n")
            else:
                self.dlog.w(f"No git diff found.")
                git_diff = None

            # Reset all edited by agent
            self.dlog(f"Reset instance repo to base commit ...")
            target_commit_hash = ctx.base_commit_hash
            assert target_commit_hash == ctx.base_commit_hash
            git_reset_cmds = [
                f"pushd {instance.repo_path}",
                f"git reset --hard HEAD~{len(ctx.applied_edits)}",
                "popd",
            ]
            instance.communicate(
                " && ".join(git_reset_cmds),
                timeout=None,
                check="raise",
                error_msg=f"Failed to rollback all {len(ctx.applied_edits)} applied edits by `{' && '.join(git_reset_cmds)}`",
            )
            ## Get &Check HEAD commit hash
            git_rev_parse_cmds = [
                f"pushd {instance.repo_path} 1>/dev/null 2>/dev/null",
                "git rev-parse HEAD",
                "popd 1>/dev/null 2>/dev/null",
            ]
            commit_hash_after_rollback = instance.communicate(
                " && ".join(git_rev_parse_cmds),
                timeout=None,
                check="raise",
                error_msg=f"Failed to get HEAD commit hash after rolling back all {len(ctx.applied_edits)} applied edits",
            ).strip()
            assert (
                commit_hash_after_rollback
                == target_commit_hash
                == instance.config.base_commit
            )
            self.dlog(f"Reset instance repo to base commit {target_commit_hash}.")
        ctx.python_session = None

        # self.dlog(f"Agent output: \n==========\n{r['output']}\n==========\n")
        result_dict = {
            "report": r["output"],
            "assertion_edit": git_diff,  # used to feedback
        }
        self._save_cache(
            data={**result_dict},
            key="safety_property_analysis_result",
            llm_query_records=recorder.records,
            agent_ctx=ctx,
        )
        return result_dict

    def _ask_impl(self, input: "Input") -> "Output":
        assert input.instance.config.lang == "c++", "only support C++ now"
        # Issue Description -1(f-l)-> Files (prompting+retrieving) -2(ele-l)-> Elements (
        # ## 1. class/struct/union/enum/macro (hold structural code, structure);
        # ## 2. function/macro (hold exceuable code, behavior)
        # ## 3. global_variable (data, data))

        issue_descriptions = {}

        # Get issue description: refer to https://github.com/SEC-bench/SWE-agent/blob/main/sweagent/run/batch_instances.py#L275
        raw_issue_description = input.instance.config.bug_report
        issue_descriptions["original"] = raw_issue_description
        (
            poc_output,
            pre_collected_context,
            static_analysis_result,
            safety_property_analysis_report,
        ) = (
            None,
            None,
            None,
            None,
        )

        def _make_enhanced_issue_description(disable_fields: list = None) -> str:
            # raw_issue_description: str,
            # poc_output: Optional[str] = None,
            # pre_collected_context: Optional[str] = None,
            # static_analysis_result: Optional[str] = None,

            disable_fields = disable_fields or []

            if (
                not poc_output
                and not pre_collected_context
                and not static_analysis_result
            ):
                return raw_issue_description

            description_parts = []

            # Add the original issue description
            if raw_issue_description:
                description_parts.append(
                    f"""\
## Issue Description written by the bug reporter

{raw_issue_description}"""
                )

            # Add the PoC output
            if poc_output and "poc" not in disable_fields:
                description_parts.append(
                    f"""\
## PoC Output obtained by actually running the PoC

The PoC triggered a crash, and the sanitizer log shows the following:
{poc_output}

**NOTE:** The PoC output above is obtained by actually running the PoC, and it may differ from the sanitizer log shown in the GitHub issue description. When analyzing the stack trace (especially the line numbers), always rely on the **PoC output** rather than the sanitizer log from the "Issue Description written by the bug reporter"."""
                )

            # Add the pre-collected context
            if pre_collected_context and "preCC" not in disable_fields:
                description_parts.append(
                    f"""\
## Context Analysis written by your co-developer

{pre_collected_context}"""
                )

            # Add the safety property analysis report
            if safety_property_analysis_report and "SPAR" not in disable_fields:
                description_parts.append(
                    f"""\
## Safety Property Analysis Report provided by your co-developer

{safety_property_analysis_report}

Note: Do not try insert other property assertions again! Your task is generating a patch to actually repair this vulnerability.
        The report is to be used to help you understand the vulnerability and repair it!
"""
                )

            return "\n\n".join(description_parts)

        if self.__enable_context_pre_collection:
            self.ilog("Pre-collecting context ...")
            pre_collected_context = self._pre_collect_context(
                instance=input.instance,
                issue_description=_make_enhanced_issue_description(),
            )
            poc_output = self._get_poc_output(instance=input.instance)
            self.dlog(f"Pre-collected context: \n===\n{pre_collected_context}\n===\n")

        sp_analysis_result = None
        if self.__enable_safety_property_analysis:
            self.ilog("Performing safety property analysis ...")
            sp_analysis_result: dict = self._perform_safety_property_analysis(
                instance=input.instance,
                issue_description=_make_enhanced_issue_description(),
            )
            safety_property_analysis_report: str = sp_analysis_result["report"]
            # safety_property_assertion_git_diff = sp_analysis_result["assertion_edit"]

            self.dlog(
                "Safety property analysis report: \n===\n"
                + f"{safety_property_analysis_report}\n===\n"
            )

        # 0. Make enhanced issue description
        enhanced_issue_description = _make_enhanced_issue_description()
        issue_descriptions["enhanced_issue_description"] = enhanced_issue_description
        issue_descriptions["poc_output"] = poc_output
        issue_descriptions["pre_collected_context"] = pre_collected_context
        issue_descriptions["static_analysis_result"] = static_analysis_result
        issue_descriptions["safety_property_analysis_result"] = sp_analysis_result
        ## do not del them
        # del poc_output
        # del pre_collected_context
        # del static_analysis_result
        # del sp_analysis_result, safety_property_analysis_report

        if os.getenv("ABL_DISABLE_PRECC_REPORT", "0") == "1":
            assert (
                "## Context Analysis written by your co-developer"
                not in enhanced_issue_description
            )
            assert pre_collected_context is None

        # 1. Locate suspicious files
        self.ilog(f"1. Loacting suspicious files...")
        file_level_loc_result = self._locate_suspicious_files(
            instance=input.instance,
            issue_description=enhanced_issue_description,
        )

        # 2. Locate suspicious code elements
        self.ilog(f"2. Loacting suspicious code elements...")
        fLLocR_copy = file_level_loc_result.copy()
        disable_fields = []
        suspicious_file_info = input.instance.get_code_structure(
            files=fLLocR_copy["suspicious_files"],
        )
        while True:
            try:
                element_level_loc_result = self._locate_suspicious_elements(
                    instance=input.instance,
                    issue_description=enhanced_issue_description,
                    file_level_loc_result=fLLocR_copy,
                    suspicious_file_info=suspicious_file_info,
                )
                break
            except ContextLengthExceededException as ex:
                self.dlog.w("Context too long; reduce suspicious files & retry")
                self.dlog.w(f">>> from: {len(fLLocR_copy['suspicious_files'])}")
                fLLocR_copy["suspicious_files"] = fLLocR_copy["suspicious_files"][:-1]
                self.dlog.w(f">>> to: {len(fLLocR_copy['suspicious_files'])}")
                if not fLLocR_copy["suspicious_files"]:
                    self.dlog.w("No suspicious files left")
                    # TRY to reduce the number of enhanced fields in issue description
                    if (
                        self.__enable_safety_property_analysis
                        and "SPAR" not in disable_fields
                    ):
                        self.dlog.w(">>> Try to disable safety property analysis")
                        disable_fields.append("SPAR")
                    elif (
                        self.__enable_context_pre_collection
                        and "preCC" not in disable_fields
                    ):
                        self.dlog.w(">>> Try to disable pre-collecting context")
                        disable_fields.append("preCC")
                    else:
                        raise RuntimeError("No suspicious files left") from ex

                    _short_id = _make_enhanced_issue_description(disable_fields)
                    assert len(_short_id) < len(enhanced_issue_description)
                    self.dlog.w(f">>>> from: {len(enhanced_issue_description)} chars")
                    self.dlog.w(f">>>> to: {len(_short_id)} chars")
                    enhanced_issue_description = _short_id
                    # retry from all suspicious files
                    fLLocR_copy = file_level_loc_result.copy()
                    self.dlog.w(">>> Retry from all suspicious files")
                    del _short_id
        del fLLocR_copy
        del disable_fields

        return self.Output(
            instance_info=input.instance.config.to_dict(),
            **file_level_loc_result,
            **element_level_loc_result,
            issue_descriptions=issue_descriptions,
        )
