import os
import re
import json
from copy import deepcopy
from dataclasses import dataclass
from collections import namedtuple
from typing import Any, List, Tuple, Dict, Set
from . import rs_utils as rsu

_COMMENT_MARK_TABLE = {"c": "//", "cpp": "//", "java": "//", "python": "#"}
_Codeblock = namedtuple("Codeblock", ["lang", "content"])
# _SearchReplacePatch = namedtuple("SearchReplacePatch", ["file", "search", "replace", "raw", "index"])
# _MultiSearchReplacePatch = namedtuple("MultiSearchReplacePatch", ["file", "patches"])


@dataclass
class _SearchReplacePatch:
    file: str
    search: str
    replace: str
    raw: str
    index: int


@dataclass
class _MultiSearchReplacePatch:
    file: str
    patches: List[_SearchReplacePatch]


class SkipException(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


def _get_comment_mark(lang: str) -> str:
    lang = lang.lower()
    assert lang in _COMMENT_MARK_TABLE
    return _COMMENT_MARK_TABLE[lang]


def _is_debug_mode() -> bool:
    return os.environ.get("DEBUG", "0") == "1"


class _DebugLog:
    def __init__(self, prefix: str = None, fback: int = 2):
        self.__prefix = prefix + " " if prefix else ""
        self.__fback = fback

    def i(self, *msg: str, **kwargs):
        self(*msg, level="i", **kwargs, fback=self.__fback + 1)

    def w(self, *msg: str, **kwargs):
        self(*msg, level="w", **kwargs, fback=self.__fback + 1)

    def __call__(self, *msg: str, level="i", **kwargs):
        if _is_debug_mode():
            log_fn = getattr(rsu, f"_{level}log", rsu._ilog)
            assert callable(log_fn)
            fback = kwargs.pop("fback", self.__fback)
            log_fn(f"{self.__prefix}[DEBUG]", *msg, fback=fback, **kwargs)


_dlog = _DebugLog()


def _add_code_block(string: str, lang: str) -> str:
    assert string is not None
    assert lang is not None
    return f"```{lang}\n{string}\n```"


def _parse_code_block(string: str, lang: str = "", strict: bool = True) -> str:
    code_pattern = rf"```{lang}\n(.*?)\n```"
    match = re.search(code_pattern, string, re.DOTALL)

    if match:
        return match.group(1)

    generic_code_pattern = r"```\n(.*?)\n```"
    match = re.search(generic_code_pattern, string, re.DOTALL)

    if match:
        return match.group(1)

    if not strict:
        return string

    raise ValueError(f"No code block found: `{string}`")


def _parse_multi_code_blocks(string: str) -> List[_Codeblock]:
    o_string = string
    string = "\n".join(
        [
            L if not L.strip().startswith("```") else L.strip()
            for L in string.split("\n")
        ]
    )
    if string != o_string:
        _dlog.w(f"Fixed with-spaces ```")
    pattern = re.compile(r"\n\s*```([^\n]*)\n(.*?)\n```\n", re.DOTALL)
    return [
        _Codeblock(
            lang=match.group(1).strip(),
            content=match.group(2),
        )
        for match in pattern.finditer("\n" + string + "\n")
    ]


def _generate_sha256_key(obj):
    import hashlib

    return hashlib.sha256(str(obj).encode()).hexdigest()


def _get_uuid() -> str:
    import uuid

    return str(uuid.uuid4())


_PL_suffix_table = {
    "c": {".c", ".h"},
    "cpp": {".cpp", ".cc", ".cxx", ".C", ".hpp", ".hh", ".hxx", ".H"},
}


def _get_PL_file_suffixes(*lang) -> Set[str]:
    suffixes = set()
    for lang in lang:
        suffixes |= _PL_suffix_table[lang]
    return suffixes


def _detect_PL_from_file_suffix(file_name: str) -> str:
    suffix = os.path.splitext(file_name)[1]
    for lang in _PL_suffix_table:
        if suffix in _PL_suffix_table[lang]:
            return lang
    return None


def _format_cpp_code(code: str | None, *, style: str = "file") -> str:
    import sys
    import shutil
    import tempfile
    import subprocess
    from pathlib import Path

    assert sys.platform == "linux"

    def _find_clang_format():
        for exe in ("clang-format",):
            path = shutil.which(exe)
            if path:
                return path
        return None

    def _find_scc():
        for exe in ("scc",):
            path = shutil.which(exe)
            if path:
                return path
        return None

    if code is None:
        return ""

    clang_format = _find_clang_format()
    if clang_format is None:
        raise RuntimeError(
            "clang-format executable not found. Install it (e.g. `brew install clang-format`, "
            "`apt install clang-format`, or `pip install clang-format`) and ensure it is in PATH."
        )
    scc = _find_scc()
    if scc is None:
        raise RuntimeError(
            "scc executable not found. Install it from https://github.com/jleffler/scc-snapshots."
        )

    with tempfile.NamedTemporaryFile("w+", suffix=".cpp", delete=False) as tmp:
        tmp.write(code)
        tmp.flush()
        result = subprocess.run(
            f"cat {tmp.name} | {scc} -S c++ | {clang_format} --style={style}",
            text=True,
            capture_output=True,
            check=False,
            shell=True,
        )
    Path(tmp.name).unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"clang-format failed (exit {result.returncode}):\n{result.stderr.strip()}"
        )

    return result.stdout


def _parse_one_search_replace_patch(
    patch: str, index: int, remove_line_marker: bool = False
) -> _SearchReplacePatch:
    if not patch.strip().startswith("###"):
        raise ValueError(f"Invalid patch, must start with '###': `{patch}`")

    search_L = None
    for i in reversed(list(range(3, 100 + 1))):  # long -> short
        search_L = f"{'<' * i} SEARCH"
        if search_L in patch:
            break
    sep_L = None
    for i in reversed(list(range(3, 100 + 1))):
        sep_L = f"{'=' * i}"
        if sep_L in patch:
            break
    replace_L = None
    for i in reversed(list(range(3, 100 + 1))):
        replace_L = f"{'>' * i} REPLACE"
        if replace_L in patch:
            break

    correct_search_L = "<<<<<<< SEARCH"
    correct_replace_L = ">>>>>>> REPLACE"
    correct_sep_L = "======="
    if search_L is not None and search_L != correct_search_L:
        _dlog.w(f"Replace search marker: `{search_L}` -> `{correct_search_L}`")
        patch = patch.replace(search_L, correct_search_L)
    if sep_L is not None and sep_L != correct_sep_L:
        _dlog.w(f"Replace separator marker: `{sep_L}` -> `{correct_sep_L}`")
        patch = patch.replace(sep_L, correct_sep_L)
    if replace_L is not None and replace_L != correct_replace_L:
        _dlog.w(f"Replace replace marker: `{replace_L}` -> `{correct_replace_L}`")
        patch = patch.replace(replace_L, correct_replace_L)

    if "<<<<<<< SEARCH" not in patch:
        raise ValueError(f"Invalid patch, must contain '<<<<<<< SEARCH': `{patch}`")
    if "=======" not in patch:
        raise ValueError(f"Invalid patch, must contain '=======': `{patch}`")
    if ">>>>>>> REPLACE" not in patch:
        raise ValueError(f"Invalid patch, must contain '>>>>>>> REPLACE': `{patch}`")

    patch_lines = patch.split("\n")
    file_line_idx = next(
        line_i
        for line_i, line in enumerate(patch_lines)
        if line.strip().startswith("###")
    )
    replace_line_idx = next(
        line_i
        for line_i, line in reversed(list(enumerate(patch_lines)))
        if line.strip().startswith(">>>>>>> REPLACE")
    )
    patch_lines = patch_lines[file_line_idx : replace_line_idx + 1]
    patch = "\n".join(patch_lines)

    patterns = [
        r"^\s*###\s*(.*?)\s*[\r\n]+\s*<<<<<<< SEARCH\s*[\r\n]+(.*?)=======\s*[\r\n]+(.*?)>>>>>>> REPLACE\s*$",
        r"^\s*###\s*(.*?)\s*[\r\n]+\s*<<<<<<< SEARCH\s*[\r\n]+(.*?)=======(.*?)>>>>>>> REPLACE\s*$",
    ]

    match = None
    for i, pattern in enumerate(patterns):
        if i != 0:
            _dlog.w(f"Pattern 0 is not useful; try pattern {i}")
        match = re.search(pattern, patch, re.DOTALL)
        if match:
            break
    if not match:
        raise ValueError(f"Patch format is invalid: `{patch}`")

    file = match.group(1).strip()
    search = match.group(2)
    replace = match.group(3)

    if search.startswith("\n"):
        search = search[1:]
    if search.endswith("\n"):
        search = search[:-1]
    if replace.startswith("\n"):
        replace = replace[1:]
    if replace.endswith("\n"):
        replace = replace[:-1]

    if remove_line_marker:
        mark_ = " // <<<<< "
        search_lines = search.splitlines()
        for i, L in enumerate(search_lines):
            if mark_ in L:
                _dlog.w(f"Remove line marker: `{mark_}`")
                new_L = L[: L.index(mark_)]
                search_lines[i] = new_L
                _dlog.w(f"[{i}] Removed line marker: `{mark_}`")
                _dlog.w(f"[{i}] >>>> from: `{L}`")
                _dlog.w(f"[{i}] >>>> to: `{new_L}`")
        search = "\n".join(search_lines)

        replace_lines = replace.splitlines()
        for i, L in enumerate(replace_lines):
            if mark_ in L:
                _dlog.w(f"Remove line marker: `{mark_}`")
                new_L = L[: L.index(mark_)]
                replace_lines[i] = new_L
                _dlog.w(f"[{i}] Removed line marker: `{mark_}`")
                _dlog.w(f"[{i}] >>>> from: `{L}`")
                _dlog.w(f"[{i}] >>>> to: `{new_L}`")
        replace = "\n".join(replace_lines)

    return _SearchReplacePatch(
        file=file,
        search=search,
        replace=replace,
        raw=patch,
        index=index,
    )


def _parse_search_replace_patch(
    patch: List[str], remove_line_marker: bool = False
) -> List[_MultiSearchReplacePatch]:
    sr_patches = [
        _parse_one_search_replace_patch(p, i, remove_line_marker)
        for i, p in enumerate(patch)
    ]
    files = set([p.file for p in sr_patches])
    file_to_patches = {}  # file -> patches
    for p in sr_patches:
        file_to_patches.setdefault(p.file, []).append(p)
    msr_pacthes = []
    for file in files:
        msr_pacthes.append(
            _MultiSearchReplacePatch(
                file=file,
                patches=file_to_patches[file],
            )
        )
    return msr_pacthes


def _collapse_ellipsis(lst: List[Any]) -> List[Any]:
    result: List[Any] = []
    prev = None
    for item in lst:
        if item.strip() == "..." and (prev and prev.strip() == "..."):
            continue
        result.append(item)
        prev = item
    return result


def _parse_c_or_cpp_code_structure_using_tree_sitter(code: str, lang: str, file: str):
    # This function has some bugs, use clang-version instead
    rsu._flog("Do NOT use this function, it has some bugs")

    # Return: {
    ##      "skeleton": ...,
    ##      "element_paths": [...], # all element paths (including class/struct's functions), no file path
    ##      "abs_element_paths": [...], # all absolute element paths (including class/struct's functions), with file path
    ##      "elements": [
    ##          {
    ##              "type": ..., # "class"/"struct"/"union"/"enum"/"function"/"macro"/"global_variable"
    ##              "name": ..., # simple name, e.g "func1"
    ##              "path": ..., # exclude file path, e.g "Class1::func1"
    ##              "abs_path": ..., # include file path, e.g "path1/file1.cxx::Class1::func1"
    ##              "code": ...,
    ##              "range": {...}, # start_line, start_column, end_line, end_column; [start, end]
    ##              "body_range": {...}, # start_line, start_column, end_line, end_column; only for node with "body"; [start, end]
    ##              "children": [...]/null, # only for class/struct's functions
    ##          }
    ##      ]
    ## }
    from tree_sitter import Language, Parser
    import tree_sitter_c, tree_sitter_cpp

    file_path = file
    del file
    _ts_type_to_element_type = {
        "class_specifier": "class",  # need to get its function (no recursive now
        "struct_specifier": "struct",  # need to get its function (no recursive now
        "union_specifier": "union",
        "enum_specifier": "enum",
        "function_definition": "function",
        "preproc_def": "macro",
        "preproc_function_def": "macro",
        "declaration": "global_variable",  # need to check more
    }

    _anonymous_id = [0]

    def _find_function_declarator(node):
        if node.type == "function_declarator":
            return node
        for child in node.children:
            if result := _find_function_declarator(child):
                return result
        return None

    def _get_name(node) -> str:
        assert node.type in {
            *_ts_type_to_element_type.keys(),
            "namespace_definition",
            "type_definition",
        }

        name = None
        if node.type == "declaration":
            if not _find_function_declarator(node):
                declarator_node = node.child_by_field_name("declarator")
                while declarator_node.type not in [
                    "identifier",
                    "qualified_identifier",
                ]:
                    declarator_node = declarator_node.child_by_field_name("declarator")
                name = declarator_node.text.decode("utf-8")
        elif node.type == "function_definition":
            _func_decl_node = _find_function_declarator(node)
            if _func_decl_node is None:
                _dlog.w("_func_decl_node is None")
                _dlog.w(f">>>> tree of node: `{node}`")
                _dlog.w(f">>>> source of node: `{node.text.decode('utf-8')}`")
                name = None
            else:
                _func_decl_node = _func_decl_node.child_by_field_name("declarator")
                name = _func_decl_node.text.decode("utf-8")
            del _func_decl_node
        elif node.type == "type_definition":

            def _find_type_identifier(n):
                if n.type == "type_identifier":
                    return n
                for child in n.children:
                    if result := _find_type_identifier(child):
                        return result
                return None

            _declarator_node = node.child_by_field_name("declarator")
            _type_id_node = _find_type_identifier(_declarator_node)
            assert _type_id_node is not None
            name = _type_id_node.text.decode("utf-8")
            del _declarator_node, _type_id_node
        elif name_node := node.child_by_field_name("name"):
            name = name_node.text.decode("utf-8")

        return name

    def _make_element_dict(
        node,
        parent_paths: str | None,
        file_path: str,
        children: list | None = None,
        type: str | None = None,
    ) -> Dict[str, Any]:
        name = _get_name(node)
        if not name:
            # llm cannot see name of anonymous element,
            # so they won't be selected,
            # so this processing is only used to avoid crashing
            ## F...ing corner cases !!!!!!!!!!!!!!!!!!
            name = f"anony_{_anonymous_id[0]}"
            _anonymous_id[0] += 1
        path = f"{parent_paths}::{name}" if parent_paths else name
        abs_path = f"{file_path}::{path}"
        body_range = None
        if body_node := node.child_by_field_name("body"):
            body_range = {
                "start_line": body_node.start_point.row + 1,
                "start_column": body_node.start_point.column + 1,
                "end_line": body_node.end_point.row + 1,
                "end_column": body_node.end_point.column + 1,
            }

        if node.type == "type_definition":
            _type_node = node.child_by_field_name("type")
            type = {
                "class_specifier": "class",
                "struct_specifier": "struct",
                "union_specifier": "union",
                "enum_specifier": "enum",
            }[_type_node.type]
            del _type_node

        return {
            "type": type or _ts_type_to_element_type[node.type],
            "name": name,
            "path": path,
            "abs_path": abs_path,
            "code": node.text.decode("utf-8"),
            "range": {
                "start_line": node.start_point.row + 1,
                "start_column": node.start_point.column + 1,
                "end_line": node.end_point.row + 1,
                "end_column": node.end_point.column + 1,
            },
            "body_range": body_range,
            "children": children,
        }

    def _process_namespace(
        ns_body_node,
        ns_path: str | None = None,
    ) -> Tuple[List[str], List[dict]]:  # element_paths, elements
        # namespace_path: 'xxx', 'xxx:yyy', ..., or '' (no namespace)

        elements = []
        element_paths = []
        for child in ns_body_node.children:
            element = None
            element2 = None

            print(ns_path, ":", child.type)
            # print(child.text.decode("utf-8"))
            # print(child)

            # Collect in-global-scope elements
            if child.type == "declaration":
                if not _find_function_declarator(child):
                    element = _make_element_dict(
                        child,
                        parent_paths=ns_path,
                        file_path=file_path,
                        type="global_variable",
                    )
            elif child.type == "type_definition":
                type_node = child.child_by_field_name("type")
                # only care typedef class/struct/union/enum [xxx] {...} yyy
                if type_node.type in {
                    "class_specifier",
                    "struct_specifier",
                    "union_specifier",
                    "enum_specifier",
                }:
                    # alias name
                    element = _make_element_dict(
                        child,
                        parent_paths=ns_path,
                        file_path=file_path,
                    )
                    # class/struct/union/enum name
                    if true_name := type_node.child_by_field_name("name"):
                        true_name_s = true_name.text.decode("utf-8")
                        element2 = deepcopy(element)
                        path_prefixes = element2["path"].split("::")[:-1]
                        apath_prefixes = element2["abs_path"].split("::")[:-1]
                        element2["name"] = true_name_s
                        element2["path"] = "::".join(path_prefixes + [true_name_s])
                        element2["abs_path"] = "::".join(apath_prefixes + [true_name_s])
                        del true_name, true_name_s
                        del path_prefixes, apath_prefixes
            elif child.type in _ts_type_to_element_type.keys():
                element = _make_element_dict(
                    child,
                    parent_paths=ns_path,
                    file_path=file_path,
                )
            else:
                # TODO: Consider template class/struct/union/function if needed
                pass  # we do not care them

            assert (
                (element is not None)
                or (child.type not in _ts_type_to_element_type)
                or child.type == "declaration"
            )

            # Collect member/static functions
            if child.type in ("class_specifier", "struct_specifier"):
                assert element2 is None
                if body_node := child.child_by_field_name("body"):
                    child_elements = []
                    for field in body_node.children:
                        if field.type == "function_definition":
                            child_elements.append(
                                _make_element_dict(
                                    field,
                                    parent_paths=element["path"],
                                    file_path=file_path,
                                )
                            )
                    element["children"] = child_elements

            # Process sub-namespace
            if child.type == "namespace_definition":
                sub_ns_name = _get_name(child)
                assert sub_ns_name is not None
                sub_ns_path = f"{ns_path}::{sub_ns_name}" if ns_path else sub_ns_name
                sub_ns_element_paths, sub_ns_elements = _process_namespace(
                    ns_body_node=child.child_by_field_name("body"),
                    ns_path=sub_ns_path,
                )
                element_paths.extend(sub_ns_element_paths)
                elements.extend(sub_ns_elements)

            if element is not None:
                element_paths.append(element["path"])
                for child_element in element["children"] or []:
                    element_paths.append(child_element["path"])
                elements.append(element)
            if element2 is not None:
                assert element is not None
                element_paths.append(element2["path"])
                for child_element in element2["children"] or []:
                    element_paths.append(child_element["path"])
                elements.append(element2)

        return element_paths, elements

    if lang not in ("c", "cpp"):
        raise ValueError(f"Unsupported language: {lang}")

    #### Make Parser ####
    C_LANGUAGE = Language(tree_sitter_c.language())
    CPP_LANGUAGE = Language(tree_sitter_cpp.language())
    parser = Parser(CPP_LANGUAGE)  # just use cpp parser
    del C_LANGUAGE, CPP_LANGUAGE

    #### Parse Code ####
    tree = parser.parse(code.encode())
    root = tree.root_node
    element_paths, elements = _process_namespace(root, ns_path=None)
    del tree, root

    #### Make Skeleton #### (Find all functions and replace their bodys with "..."
    # TODO: Consider maro function if needed
    def _collect_all_function_elements(elts: List[dict] | None) -> List[dict]:
        function_elements = []
        for elt in elts or []:
            if elt["type"] == "function":
                function_elements.append(elt)
            else:
                function_elements.extend(
                    _collect_all_function_elements(elt["children"])
                )
        return function_elements

    skeleton_lines = code.splitlines()
    function_elements = _collect_all_function_elements(elements)
    for fn_ele in function_elements:
        body_range = fn_ele["body_range"]
        if body_range is None:
            continue  # skip no-body functions
        br_start_line = body_range["start_line"]
        br_end_line = body_range["end_line"]
        start_l_idx = (br_start_line + 1) - 1  # 1-based -> ignore self -> 0-based
        end_l_idx = (br_end_line - 1) - 1  # 1-based -> ignore self -> 0-based
        assert start_l_idx > 0 and start_l_idx < len(skeleton_lines)
        assert end_l_idx > 0 and end_l_idx < len(skeleton_lines)
        for line_idx in range(start_l_idx, end_l_idx + 1):
            _line = skeleton_lines[line_idx]
            _leading_spaces = _line[: len(_line) - len(_line.lstrip())]
            skeleton_lines[line_idx] = _leading_spaces + "..."
            del _line, _leading_spaces
    skeleton_lines = _collapse_ellipsis(skeleton_lines)
    skeleton = "\n".join(skeleton_lines)

    return {
        "skeleton": skeleton,
        "element_paths": element_paths,
        "abs_element_paths": [f"{file_path}::{p}" for p in element_paths],
        "elements": elements,
    }


# Sanitizer error message patterns for detection
_SANITIZER_ERROR_PATTERNS: List[str] = [
    "ERROR: AddressSanitizer:",
    "ERROR: MemorySanitizer:",
    "WARNING: MemorySanitizer:",
    "UndefinedBehaviorSanitizer:DEADLYSIGNAL",
    "ERROR: LeakSanitizer:",
    "SUMMARY: UndefinedBehaviorSanitizer: undefined-behavior",
]

# Sanitizer report parsing patterns
_SANITIZER_START_PATTERN: str = r"==\d+==(?:ERROR|WARNING): (\w+)Sanitizer:"
_SANITIZER_END_PATTERN: str = r"==\d+==ABORTING"
_STACK_TRACE_END_PATTERN: str = r"\s+#\d+ 0x[0-9a-f]+"


def _extract_sanitizer_report(container_output: str) -> str | None:
    """Extract the sanitizer report from container output using regex.

    Args:
        container_output: Container log output to process.

    Returns:
        Extracted sanitizer report or None if no report found.
    """
    if not container_output:
        return None

    # Look for complete sanitizer report with both start and end patterns
    start_match = re.search(_SANITIZER_START_PATTERN, container_output)
    end_match = re.search(_SANITIZER_END_PATTERN, container_output)

    if start_match and end_match:
        # Get the start and end positions of the report
        start_pos = start_match.start()
        end_pos = end_match.end()

        # Make sure end_pos comes after start_pos
        if end_pos > start_pos:
            # Extract the complete report
            return container_output[start_pos:end_pos]

    # If we have a start match but no end match, try to find the last stack trace line
    if start_match and not end_match:
        start_pos = start_match.start()
        # Find all stack trace lines
        stack_trace_matches = list(
            re.finditer(_STACK_TRACE_END_PATTERN, container_output[start_pos:])
        )
        if stack_trace_matches:
            # Use the last stack trace line as the end point (plus some buffer)
            last_match = stack_trace_matches[-1]
            end_pos = (
                # Find the position after the last stack trace match
                start_pos
                + last_match.end()
            )
            # Find the next newline after the last stack trace match
            next_newline_pos = container_output.find("\n", end_pos)
            if next_newline_pos != -1:
                end_pos = next_newline_pos + 1  # Include the newline
            end_pos = min(end_pos, len(container_output))
            return container_output[start_pos:end_pos]

    # If we can't find a complete report, check if any sanitizer indicators exist
    if any(indicator in container_output for indicator in _SANITIZER_ERROR_PATTERNS):
        # Extract context around the first indicator found
        for indicator in _SANITIZER_ERROR_PATTERNS:
            if indicator in container_output:
                idx = container_output.find(indicator)
                # Get up to 1000 characters before and after the indicator
                start_idx = max(0, idx - 1000)
                end_idx = min(len(container_output), idx + 1000)
                return container_output[start_idx:end_idx]

    return None


def _extract_stack_frames_from_sanitizer_log(log_text: str) -> List[dict]:
    """
    Parse all stack frames from sanitizer log text.

    Args:
        log_text: The sanitizer log text containing stack traces

    Returns:
        List[dict]: List of all stack frames, each containing:
            - index: Stack frame number (e.g., 0, 1, 2)
            - address: Memory address (e.g., "0x5b1e6ae2d360")
            - function: Function name (e.g., "QuickCheckForUnpoisonedRegion")
            - file: File path (e.g., "/src/llvm-project/compiler-rt/lib/asan/asan_interceptors_memintrinsics.h")
            - line: Line number (e.g., 37)
            - col: Column number (e.g., 7)
    """
    stack_frames = []

    # Regular expression to match stack frame lines
    # Format: #0 0x5b1e6ae2d360 in function_name /path/to/file:line:col
    stack_pattern = r"^\s*#(\d+)\s+(0x[0-9a-f]+)\s+in\s+([^\s]+)\s+(.+):(\d+):(\d+)$"

    lines = log_text.split("\n")

    for line in lines:
        line = line.strip()
        match = re.match(stack_pattern, line)
        if match:
            index, address, function, file_path, line_num, col_num = match.groups()

            stack_frame = {
                "index": int(index),
                "address": address,
                "function": function,
                "file": file_path,
                "line": int(line_num),
                "col": int(col_num),
            }
            stack_frames.append(stack_frame)

    return stack_frames


def _parse_git_diff(patch_text: str) -> List[Dict[str, Any]]:

    results = []
    current_file = None

    file_re = re.compile(r"^\+\+\+ b/(.+)$")
    hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    for line in patch_text.splitlines():
        file_match = file_re.match(line)
        if file_match:
            current_file = file_match.group(1)
            results.append({"file": current_file, "hunks": []})
            continue

        hunk_match = hunk_re.match(line)
        if hunk_match and current_file:
            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4)) if hunk_match.group(4) else 1

            results[-1]["hunks"].append(
                {
                    "old_start": old_start,
                    "old_count": old_count,
                    "new_start": new_start,
                    "new_count": new_count,
                }
            )

    return results


def _get_git_diff(
    file_paths: List[str],
    old_contents: List[str],
    new_contents: List[str],
) -> str:
    assert len(file_paths) == len(old_contents) == len(new_contents)

    import subprocess

    try:
        # Generate a temperary folder and add uuid to avoid collision
        repo_playground = rsu._make_temp_dir()
        # Create playground
        os.makedirs(repo_playground, exist_ok=True)

        # Create a fake git repo
        subprocess.run(
            f"cd {repo_playground} && git init",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for file_path, old_content, new_content in zip(
            file_paths, old_contents, new_contents, strict=True
        ):
            subprocess.run(
                f"mkdir -p {repo_playground}/{os.path.dirname(file_path)}",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            with open(f"{repo_playground}/{file_path}", "w") as f:
                f.write(old_content)

            # Add file to git (same message is okay
            subprocess.run(
                f"cd {repo_playground} && git add {file_path} && git commit -m 'initial commit'",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        for file_path, old_content, new_content in zip(
            file_paths, old_contents, new_contents, strict=True
        ):
            # Edit file
            with open(f"{repo_playground}/{file_path}", "w") as f:
                f.write(new_content)

        # Get git diff
        o = subprocess.run(
            f"cd {repo_playground} && git diff .", shell=True, capture_output=True
        )

        s = o.stdout.decode("utf-8")

    finally:
        # Remove playground
        subprocess.run(f"rm -rf {repo_playground}", shell=True)

    return s


def _get_codeql_homes() -> dict:
    codeql_cli_home = os.path.abspath(os.getenv("CODEQL_CLI_HOME"))
    codeql_repo_home = os.path.abspath(os.getenv("CODEQL_REPO_HOME"))
    codeql_cache_dir = os.path.abspath(os.getenv("CODEQL_CACHE_DIR"))
    if not os.path.isdir(codeql_cli_home):
        raise ValueError(f"CODEQL_CLI_HOME is not a directory: {codeql_cli_home}")
    if not os.path.isdir(codeql_repo_home):
        raise ValueError(f"CODEQL_REPO_HOME is not a directory: {codeql_repo_home}")
    if not os.path.isdir(codeql_cache_dir):
        raise ValueError(f"CODEQL_CACHE_DIR is not a directory: {codeql_cache_dir}")

    return {
        "codeql_cli_home": codeql_cli_home,
        "codeql_repo_home": codeql_repo_home,
        "codeql_cache_dir": codeql_cache_dir,
    }


def _get_codeql_security_query_path(repo_path: str, language: str) -> str:
    language = {"c++": "cpp"}.get(language, language)
    return {
        "cpp": f"{repo_path}/cpp/ql/src/codeql-suites/cpp-security-extended.qls",
    }[language]


def _run_clang_static_analyzer(instance, checker: str) -> dict:
    checker_groups = {
        "buffer_overrun": [
            "cwe-top-25-2024:cwe-125",
            "cwe-top-25-2024:cwe-787",
            "cwe-top-25-2024:cwe-119",
            "alpha.security.ArrayBound",
            "alpha.security.ArrayBoundV2",
            "alpha.security.MallocOverflow",
            "alpha.security.ReturnPtrRange",
            "alpha.security.taint.TaintPropagation",
            "alpha.unix.cstring.BufferOverlap",
            "alpha.unix.cstring.NotNullTerminated",
            "alpha.unix.cstring.OutOfBounds",
            "alpha.unix.cstring.UninitializedRead",
            "core.StackAddressEscape",
            "core.VLASize",
            "core.uninitialized.ArraySubscript",
            "core.uninitialized.NewArraySize",
            "cplusplus.PlacementNew",
            "cplusplus.StringChecker",
            # "security.insecureAPI.DeprecatedOrUnsafeBufferHandling",
            # "security.insecureAPI.bcopy",
            # "security.insecureAPI.gets",
            # "security.insecureAPI.strcpy",
            "unix.MallocSizeof",
            "unix.cstring.BadSizeArg",
            "unix.cstring.NullArg",
        ],
        "null_pointer_dereference": [
            "core.NullDereference",
            "core.CallAndMessage",
            "core.NonNullParamChecker",
            "nullability.NullPassedToNonnull",
            "nullability.NullReturnedFromNonnull",
            "unix.cstring.NullArg",
            "nullability.NullableDereferenced",
            "nullability.NullablePassedToNonnull",
            "nullability.NullableReturnedFromNonnull",
            "alpha.cplusplus.SmartPtr",
            "osx.cocoa.NilArg",
            "osx.coreFoundation.CFRetainRelease",
            "alpha.core.FixedAddr",
            "alpha.core.TestAfterDivZero",
            "alpha.security.ReturnPtrRange",
            "alpha.unix.cstring.NotNullTerminated",
        ],
    }

    if os.getenv("PRE_GENERATE_COMPILE_DB", "0") == "0":
        raise ValueError("PRE_GENERATE_COMPILE_DB must be set to 1")

    checkers = checker_groups[checker]
    checkers_args = " ".join(
        [
            "--disable-all",
            *[f"--enable {checker}" for checker in checkers],
        ]
    )
    repo_path = instance.repo_path
    log = instance.communicate(
        f"bash {instance.config.STATIC_ANALYSIS_TOOLS_DIR}/run_csa.sh {repo_path} {checkers_args}",
        timeout=None,
        check="raise",
        error_msg="Failed to run clang static analyzer",
    )

    _text_result_file = f"{repo_path}/clang_static_analyzer_result.txt"
    _json_result_file = f"{repo_path}/clang_static_analyzer_result.json"
    text_result = instance.read_file(_text_result_file)
    json_result = json.loads(instance.read_file(_json_result_file))
    del _text_result_file, _json_result_file

    try:
        _start_summary_line = "----==== Severity Statistics ====----"
        _summary_lines = [L.rstrip() for L in text_result.splitlines()]
        _summary_start_idx = _summary_lines.index(_start_summary_line)
        summary = "\n".join(_summary_lines[_summary_start_idx:])
        del _start_summary_line, _summary_lines, _summary_start_idx
    except:
        summary = None

    return {
        "log": log,
        "result": {
            "text": text_result,
            "json": json_result,
            "summary": summary,
        },
    }


def _run_facebook_infer(instance, checker: str) -> dict:
    checker_groups = {
        "buffer_overrun": [
            "--bufferoverrun-only",
        ],
        "null_pointer_dereference": [
            "--pulse-only",
            "--enable-issue-type NULLPTR_DEREFERENCE",
            "--enable-issue-type NULLPTR_DEREFERENCE_IN_NULLSAFE_CLASS",
            "--enable-issue-type NULLPTR_DEREFERENCE_IN_NULLSAFE_CLASS_LATENT",
            "--enable-issue-type NULLPTR_DEREFERENCE_LATENT",
            # ---------------------------------------------------
            "--disable-issue-type BAD_ARG",
            "--disable-issue-type BAD_ARG_LATENT",
            "--disable-issue-type BAD_GENERATOR",
            "--disable-issue-type BAD_GENERATOR_LATENT",
            "--disable-issue-type BAD_KEY",
            "--disable-issue-type BAD_KEY_LATENT",
            "--disable-issue-type BAD_MAP",
            "--disable-issue-type BAD_MAP_LATENT",
            "--disable-issue-type BAD_RECORD",
            "--disable-issue-type BAD_RECORD_LATENT",
            "--disable-issue-type BAD_RETURN",
            "--disable-issue-type BAD_RETURN_LATENT",
            "--disable-issue-type CONFIG_USAGE",
            "--disable-issue-type CONSTANT_ADDRESS_DEREFERENCE",
            "--disable-issue-type CONSTANT_ADDRESS_DEREFERENCE_LATENT",
            "--disable-issue-type DATA_FLOW_TO_SINK",
            "--disable-issue-type MEMORY_LEAK_C",
            "--disable-issue-type MEMORY_LEAK_CPP",
            "--disable-issue-type MUTUAL_RECURSION_CYCLE",
            "--disable-issue-type NIL_BLOCK_CALL",
            "--disable-issue-type NIL_BLOCK_CALL_LATENT",
            "--disable-issue-type NIL_INSERTION_INTO_COLLECTION",
            "--disable-issue-type NIL_INSERTION_INTO_COLLECTION_LATENT",
            "--disable-issue-type NIL_MESSAGING_TO_NON_POD",
            "--disable-issue-type NIL_MESSAGING_TO_NON_POD_LATENT",
            "--disable-issue-type NO_MATCHING_BRANCH_IN_TRY",
            "--disable-issue-type NO_MATCHING_BRANCH_IN_TRY_LATENT",
            "--disable-issue-type NO_MATCHING_CASE_CLAUSE",
            "--disable-issue-type NO_MATCHING_CASE_CLAUSE_LATENT",
            "--disable-issue-type NO_MATCHING_ELSE_CLAUSE",
            "--disable-issue-type NO_MATCHING_ELSE_CLAUSE_LATENT",
            "--disable-issue-type NO_MATCHING_FUNCTION_CLAUSE",
            "--disable-issue-type NO_MATCHING_FUNCTION_CLAUSE_LATENT",
            "--disable-issue-type NO_MATCH_OF_RHS",
            "--disable-issue-type NO_MATCH_OF_RHS_LATENT",
            "--disable-issue-type NO_TRUE_BRANCH_IN_IF",
            "--disable-issue-type NO_TRUE_BRANCH_IN_IF_LATENT",
            "--disable-issue-type NULL_ARGUMENT",
            "--disable-issue-type NULL_ARGUMENT_LATENT",
            "--disable-issue-type OPTIONAL_EMPTY_ACCESS",
            "--disable-issue-type OPTIONAL_EMPTY_ACCESS_LATENT",
            "--disable-issue-type PULSE_CANNOT_INSTANTIATE_ABSTRACT_CLASS",
            "--disable-issue-type PULSE_CONST_REFABLE",
            "--disable-issue-type PULSE_DICT_MISSING_KEY",
            "--disable-issue-type PULSE_DYNAMIC_TYPE_MISMATCH",
            "--disable-issue-type PULSE_READONLY_SHARED_PTR_PARAM",
            "--disable-issue-type PULSE_REFERENCE_STABILITY",
            "--disable-issue-type PULSE_RESOURCE_LEAK",
            "--disable-issue-type PULSE_TRANSITIVE_ACCESS",
            "--disable-issue-type PULSE_UNAWAITED_AWAITABLE",
            "--disable-issue-type PULSE_UNINITIALIZED_CONST",
            "--disable-issue-type PULSE_UNINITIALIZED_VALUE",
            "--disable-issue-type PULSE_UNNECESSARY_COPY",
            "--disable-issue-type PULSE_UNNECESSARY_COPY_ASSIGNMENT",
            "--disable-issue-type PULSE_UNNECESSARY_COPY_ASSIGNMENT_CONST",
            "--disable-issue-type PULSE_UNNECESSARY_COPY_ASSIGNMENT_MOVABLE",
            "--disable-issue-type PULSE_UNNECESSARY_COPY_INTERMEDIATE",
            "--disable-issue-type PULSE_UNNECESSARY_COPY_INTERMEDIATE_CONST",
            "--disable-issue-type PULSE_UNNECESSARY_COPY_MOVABLE",
            "--disable-issue-type PULSE_UNNECESSARY_COPY_OPTIONAL",
            "--disable-issue-type PULSE_UNNECESSARY_COPY_OPTIONAL_CONST",
            "--disable-issue-type PULSE_UNNECESSARY_COPY_RETURN",
            "--disable-issue-type RETAIN_CYCLE",
            "--disable-issue-type RETAIN_CYCLE_NO_WEAK_INFO",
            "--disable-issue-type SENSITIVE_DATA_FLOW",
            "--disable-issue-type STACK_VARIABLE_ADDRESS_ESCAPE",
            "--disable-issue-type TAINT_ERROR",
            "--disable-issue-type USE_AFTER_DELETE",
            "--disable-issue-type USE_AFTER_DELETE_LATENT",
            "--disable-issue-type USE_AFTER_FREE",
            "--disable-issue-type USE_AFTER_FREE_LATENT",
            "--disable-issue-type USE_AFTER_LIFETIME",
            "--disable-issue-type USE_AFTER_LIFETIME_LATENT",
            "--disable-issue-type VECTOR_INVALIDATION",
            "--disable-issue-type VECTOR_INVALIDATION_LATENT",
        ],
    }

    if os.getenv("PRE_GENERATE_COMPILE_DB", "0") == "0":
        raise ValueError("PRE_GENERATE_COMPILE_DB must be set to 1")

    checkers_args = " ".join(checker_groups[checker])

    from .instance import USE_LOCAL_DEPLOYMENT

    if USE_LOCAL_DEPLOYMENT:
        _dlog.w("Using local deployment, limit infer to use 1 thread")
        _dlog.w('>> To avoid "Unix.Unix_error "No such file or directory" connect ..."')
        checkers_args += "-j 1"

    repo_path = instance.repo_path
    log = instance.communicate(
        f"bash {instance.config.STATIC_ANALYSIS_TOOLS_DIR}/run_infer.sh {repo_path} {checkers_args}",
        timeout=None,
        check="raise",
        error_msg="Failed to run facebook infer",
    )

    _text_result_file = f"{repo_path}/infer-out/report.txt"
    _json_result_file = f"{repo_path}/infer-out/report.json"
    # _sarif_result_file = f"{repo_path}/infer-out/report.sarif" # v1.1.0 not support --sarif
    _pmd_xml_result_file = f"{repo_path}/infer-out/report.xml"
    text_result = instance.read_file(_text_result_file)
    json_result = json.loads(instance.read_file(_json_result_file))
    # sarif_result = json.loads(instance.read_file(_sarif_result_file))
    pmd_xml_result = instance.read_file(_pmd_xml_result_file)
    del _text_result_file
    del _json_result_file
    # del _sarif_result_file
    del _pmd_xml_result_file

    try:
        _summary_lines = [L.rstrip() for L in text_result.splitlines()]
        _start_summary_line_idx = next(
            i
            for i, L in reversed(list(enumerate(_summary_lines)))
            if re.fullmatch(r"Found \d+ issues", L)
        )
        summary = "\n".join(_summary_lines[_start_summary_line_idx:])
        del _summary_lines, _start_summary_line_idx
    except:
        summary = None

    return {
        "log": log,
        "result": {
            "text": text_result,
            "json": json_result,
            # "sarif": sarif_result,
            "pmd_xml": pmd_xml_result,
            "summary": summary,
        },
    }


def _run_cppcheck(instance, checker: str) -> dict:
    checker_groups = {
        "buffer_overrun": [
            "cppcheck-arrayIndexOutOfBounds",
            "cppcheck-arrayIndexOutOfBoundsCond",
            "cppcheck-bufferAccessOutOfBounds",
            "cppcheck-containerOutOfBounds",
            "cppcheck-negativeIndex",
            "cppcheck-negativeContainerIndex",
            "cppcheck-stlOutOfBounds",
            "cppcheck-pointerOutOfBounds",
            "cppcheck-pointerOutOfBoundsCond",
            "cppcheck-invalidPointerCast",
            "cppcheck-pointerSize",
            "cppcheck-argumentSize",
            "cppcheck-invalidScanfFormatWidth",
            "cppcheck-incompleteArrayFill",
            "cppcheck-stringLiteralWrite",
            "cppcheck-derefInvalidIterator",
            "cppcheck-invalidIterator1",
            "cppcheck-eraseIteratorOutOfBounds",
            "cppcheck-stlBoundaries",
            "cppcheck-uninitstring",
            "cppcheck-stlcstr",
            "cppcheck-uninitdata",
            "cppcheck-uninitvar",
            "cppcheck-objectIndex",
        ],
        "null_pointer_dereference": [
            "cppcheck-nullPointer",
            "cppcheck-nullPointerArithmetic",
            "cppcheck-nullPointerArithmeticRedundantCheck",
            "cppcheck-nullPointerOutOfMemory",
            "cppcheck-nullPointerOutOfResources",
            "cppcheck-nullPointerDefaultArg",
            "cppcheck-nullPointerRedundantCheck",
            "cppcheck-ctunullpointer",
        ],
    }

    if os.getenv("PRE_GENERATE_COMPILE_DB", "0") == "0":
        raise ValueError("PRE_GENERATE_COMPILE_DB must be set to 1")

    checkers = checker_groups[checker]
    checkers_args = " ".join(
        [
            "--disable-all",
            *[f"--enable {checker}" for checker in checkers],
        ]
    )
    repo_path = instance.repo_path
    log = instance.communicate(
        f"bash {instance.config.STATIC_ANALYSIS_TOOLS_DIR}/run_cppcheck.sh {repo_path} {checkers_args}",
        timeout=None,
        check="raise",
        error_msg="Failed to run clang static analyzer",
    )

    _text_result_file = f"{repo_path}/cppcheck_result.txt"
    _json_result_file = f"{repo_path}/cppcheck_result.json"
    text_result = instance.read_file(_text_result_file)
    json_result = json.loads(instance.read_file(_json_result_file))
    del _text_result_file, _json_result_file

    try:
        _start_summary_line = "----==== Severity Statistics ====----"
        _summary_lines = [L.rstrip() for L in text_result.splitlines()]
        _summary_start_idx = _summary_lines.index(_start_summary_line)
        summary = "\n".join(_summary_lines[_summary_start_idx:])
        del _start_summary_line, _summary_lines, _summary_start_idx
    except:
        summary = None

    return {
        "log": log,
        "result": {
            "text": text_result,
            "json": json_result,
            "summary": summary,
        },
    }


def _parse_codechecker_checker_details(text: str) -> dict:
    result = {}

    blocks = text.strip().split("\n\n")

    for block in blocks:
        lines = block.strip().split("\n")
        if not lines:
            continue

        checker_name = lines[0].strip()
        description_lines = []
        for line in lines[1:]:
            if not line.strip().startswith("Status:"):
                description_lines.append(line)

        description = "\n".join(description_lines)
        enhanced_description = f"""\
{checker_name}
{description}""".strip()

        result[checker_name] = {
            "name": checker_name,
            "description": enhanced_description,
            "_description": description,
        }

    return result


def _get_clang_static_analyzer_checker_details() -> dict:
    this_dir = os.path.dirname(os.path.abspath(__file__))
    assets_dir = os.path.join(this_dir, "_assets")
    detail_fn = os.path.join(assets_dir, "clang_static_analyzer_all_checkers.txt")
    with open(detail_fn, "r") as f:
        text = f.read()
    checker_details = _parse_codechecker_checker_details(text)
    return checker_details


def _parse_facebook_infer_issue_types_markdown(markdown_content: str) -> dict:
    checker_details = {}

    # Split the markdown content by sections starting with ##
    sections = re.split(r"\n## ", markdown_content)

    for section in sections:
        if not section.strip():
            continue

        # Extract the issue type name (first line after ##)
        lines = section.split("\n")
        if not lines:
            continue

        # The first line should be the issue type name
        issue_type_line = lines[0].strip()
        if not issue_type_line:
            continue

        # Extract the issue type name (remove any trailing asterisks or other formatting)
        issue_type_name = issue_type_line.split()[0].strip()

        # Skip if this is the title section (not an issue type)
        if issue_type_name.lower() in ["list", "title:", "overview"]:
            continue

        # The rest of the section is the description
        description = "\n".join(lines[1:]).strip()

        # Skip if we don't have a valid issue type name or description
        if issue_type_name and description:
            checker_details[issue_type_name] = {
                "name": issue_type_name,
                "description": description,
            }

    return checker_details


def _get_facebook_infer_checker_details() -> dict:
    this_dir = os.path.dirname(os.path.abspath(__file__))
    assets_dir = os.path.join(this_dir, "_assets")
    detail_md_file = os.path.join(assets_dir, "facebook_infer_all_issue_types.md")

    # Read the markdown file
    with open(detail_md_file, "r", encoding="utf-8") as f:
        markdown_content = f.read()

    # Parse the markdown content
    checker_details = _parse_facebook_infer_issue_types_markdown(markdown_content)
    return checker_details


def _get_cppcheck_checker_details() -> dict:
    this_dir = os.path.dirname(os.path.abspath(__file__))
    assets_dir = os.path.join(this_dir, "_assets")
    detail_fn = os.path.join(assets_dir, "cppcheck_all_checkers.txt")
    with open(detail_fn, "r") as f:
        text = f.read()
    checker_details = _parse_codechecker_checker_details(text)
    return checker_details


def _analyze_no_attr_python_function_calls(
    code: str,
    target_funcs: List[str],
) -> Dict[str, Dict[str, Any]]:
    """
    Analyze whether given functions (no-attribute calls only) are called in the Python code.
    e.g. 'foo()' counts, but 'obj.foo()' or 'pkg.foo()' do NOT.

    Args:
        code (str): Python source code.
        target_funcs (List[str]): List of function names to check.

    Returns:
        Dict[str, Dict[str, Any]]: Mapping funcname -> {"name": name, "called": bool}
    """

    import ast

    result = {name: {"name": name, "called": False} for name in target_funcs}

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"Invalid Python code: {e}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_node = node.func
            if isinstance(func_node, ast.Name):
                func_name = func_node.id
                if func_name in result:
                    result[func_name]["called"] = True

    return result


def _make_enhanced_report_for_codechecker(
    instance,
    jsons: List[dict],
    texts: List[str],
    tool_name: str,
) -> str:
    def _get_file_path_to_show(abs_file_path: str) -> str:
        rel_file_path = os.path.relpath(abs_file_path, start=instance.repo_path)
        return f"{instance.repo_name}/{rel_file_path}"

    if not jsons:
        assert not texts, "Not reached"
        assert not jsons and not texts

    if not jsons and not texts:
        return ""

    CONTEXT_WINDOW = 3
    bug_path_marker_fmt = " // <-- {report_id}, {bug_event_id}, {file_path_to_show}:{lineno}:{colno}: {message}"
    macro_expan_marker_fmt = " // <-- {report_id}, {file_path_to_show}:{lineno}:{colno}: Expanded `{macro_name}` to `{message}`"

    # file_path(abs) -> [{file_path_to_show: ..., lineno: ..., colno: ..., message: ..., ...}, ...]
    file2marks = {}
    # [checker1, ...]
    checkers = {}  # check_name => [used_report_id0, ...]
    for report_idx, json_report in enumerate(jsons, start=1):
        report_id = f"${report_idx}"
        json_report["report_id"] = report_id
        json_report.pop("bug_path_positions", None)
        checkers.setdefault(json_report["checker_name"], []).append(report_id)
        for bug_event_idx, e in enumerate(json_report["bug_path_events"], start=1):
            abs_file_path = e["file"]["path"]
            file_path_to_show = _get_file_path_to_show(abs_file_path)
            file2marks.setdefault(abs_file_path, []).append(
                {
                    "marker_fmt": bug_path_marker_fmt,
                    "report_id": report_id,
                    "bug_event_id": bug_event_idx,
                    "file_path_to_show": file_path_to_show,
                    "lineno": e["line"],
                    "colno": e["column"],
                    "message": e["message"],
                }
            )
        for macro_expan in json_report["macro_expansions"]:
            abs_file_path = macro_expan["file"]["path"]
            file_path_to_show = _get_file_path_to_show(abs_file_path)
            file2marks.setdefault(abs_file_path, []).append(
                {
                    "marker_fmt": macro_expan_marker_fmt,
                    "report_id": report_id,
                    "file_path_to_show": file_path_to_show,
                    "lineno": macro_expan["line"],
                    "colno": macro_expan["column"],
                    "macro_name": macro_expan["name"],
                    "message": macro_expan["message"],
                }
            )

    # file_path(abs) -> {file_path(repo/rel_path): ..., context_lines: [...,]}
    file2context = {}
    for abs_file_path, marks in file2marks.items():
        file_path_to_show = _get_file_path_to_show(abs_file_path)
        original_content: str = instance.read_file(abs_file_path)
        original_lines = original_content.splitlines()
        context_lines = ["\n...\n"] * len(original_lines)
        for marker in marks:
            lineno = marker["lineno"]
            line_idx = lineno - 1  # 1-based -> 0-based index
            start_idx = max(0, line_idx - CONTEXT_WINDOW)  # [start_idx, end_idx)
            end_idx = min(len(original_lines), line_idx + CONTEXT_WINDOW + 1)
            assert 0 <= line_idx < len(original_lines)
            assert 0 <= start_idx < end_idx <= len(original_lines)
            assert end_idx - start_idx <= CONTEXT_WINDOW * 2 + 1
            context_lines[start_idx:end_idx] = original_lines[start_idx:end_idx]
        for marker in marks:
            marker = marker.copy()
            marker_fmt = marker.pop("marker_fmt")
            lineno = marker["lineno"]
            line_idx = lineno - 1  # 1-based -> 0-based index
            context_lines[line_idx] += marker_fmt.format_map(marker)
        context_lines = _collapse_ellipsis(context_lines)
        assert abs_file_path not in file2context
        file2context[abs_file_path] = {
            "file_path": file_path_to_show,
            "context_lines": context_lines,
        }

    context_with_bug_paths_lines = []
    for i, (abs_file_path, context) in enumerate(file2context.items(), start=1):
        file_path_to_show = context["file_path"]
        context_lines = context["context_lines"]
        lang = _detect_PL_from_file_suffix(abs_file_path) or ""
        context_with_bug_paths_lines.append(f"({i}) File {file_path_to_show}:")
        context_with_bug_paths_lines.append(f"```{lang}")
        context_with_bug_paths_lines.extend(context_lines)
        context_with_bug_paths_lines.append(f"```")
        context_with_bug_paths_lines.append("")
    context_with_bug_paths = "\n".join(context_with_bug_paths_lines)
    del context_with_bug_paths_lines

    json_report = json.dumps(jsons, indent=2)
    text_report = "\n\n\n".join(
        f"{idx}.\n{t.strip()}" for idx, t in enumerate(texts, start=1)
    )
    text_report_section = (
        f"""\
#### Text Report
```text
{text_report}
```"""
        if texts
        else ""
    )

    all_checker_details = {
        "Clang Static Analyzer": _get_clang_static_analyzer_checker_details,
        "Cppcheck": _get_cppcheck_checker_details,
    }[tool_name]()
    checker_details_lines = []
    for i, (check_name, used_report_ids) in enumerate(checkers.items(), start=1):
        checker_details_lines.append(
            f"({i}) {check_name}: (reported by {', '.join(used_report_ids)})"
        )
        checker_details_lines.append(
            f"{all_checker_details.get(check_name, {}).get('description', 'No description')}"
        )
        checker_details_lines.append("")
    checker_details = "\n".join(checker_details_lines)
    del checker_details_lines

    return f"""\
### Analysis Report from {tool_name}

#### JSON Report
```json
{json_report}
```

{text_report_section}

#### Context Annotated with Bug Paths (and Macro Expansions)

**NOTE**:
[1] Only key lines are shown; other lines are replaced with `...`.
[2] The line markers in the context annotated with bug paths are as follows:
    - "// <-- {{report_id}}, {{bug_event_id}}, {{file_path}}:{{lineno}}:{{colno}}: {{message}}": Indicates a bug path event.
[3] The line markers in the context annotated with macro expansions are as follows:
    - "// <-- {{report_id}}, {{file_path}}:{{lineno}}:{{colno}}: Expanded `{{macro_name}}` to `{{message}}`": Indicates a macro expansion.

{context_with_bug_paths}

#### Checker Details
{checker_details}
"""


def _make_enahanced_report_for_clang_static_analyzer(
    instance,
    jsons: List[dict],
    texts: List[str],
) -> str:
    return _make_enhanced_report_for_codechecker(
        instance,
        jsons,
        texts,
        tool_name="Clang Static Analyzer",
    )


def _make_enahanced_report_for_facebook_infer(
    instance,
    jsons: List[dict],
    texts: List[str],
) -> str:
    def _extract_see_links(text: str) -> List[Tuple[str, str]]:
        pattern = r"See\s*\[([^\]]+)\]\(#([^)]+)\)"
        matches = re.findall(pattern, text)
        return matches

    if not jsons:
        assert not texts, "Not reached"
        assert not jsons and not texts

    if not jsons and not texts:
        return ""

    CONTEXT_WINDOW = 3
    line_marker_fmt = "// <-- {report_id}, bug_trace_item_id={bug_trace_item_id}, level={level}, {filename}:{line_number}:{column_number}: {description}"

    # file_path(abs) -> [(report_id, bug_trace_item_id, level, filename, line_number, column_number, description), ...]
    file2marks = {}
    # bug_type => [repoted_by_0, ...]
    bug_types = {}
    for report_idx, json_report in enumerate(jsons, start=1):
        report_id = f"${report_idx}"
        bug_type = json_report["bug_type"]
        json_report["report_id"] = report_id
        bug_types.setdefault(bug_type, []).append(report_id)
        for bug_trace_item_idx, bug_trace_item in enumerate(json_report["bug_trace"]):
            bug_trace_item_id = f"@{bug_trace_item_idx}"
            file_path = bug_trace_item["filename"]
            line_number = bug_trace_item["line_number"]
            column_number = bug_trace_item["column_number"]
            description = bug_trace_item["description"]
            level = bug_trace_item["level"]
            bug_trace_item["bug_trace_item_id"] = bug_trace_item_id
            file2marks.setdefault(file_path, []).append(
                (
                    report_id,
                    bug_trace_item_id,
                    level,
                    file_path,
                    line_number,
                    column_number,
                    description,
                )
            )

    # file_path(abs) -> {file_path(repo/filename): ..., context_lines: [...,]}
    file2context = {}
    for file_path, marks in file2marks.items():
        file_path_to_show = f"{instance.repo_name}/{file_path}"
        abs_file_path = f"{instance.repo_path}/{file_path}"
        original_content: str = instance.read_file(abs_file_path)
        original_lines = original_content.splitlines()
        context_lines = ["\n...\n"] * len(original_lines)
        for _, _, _, _, line_number, _, _ in marks:
            line_idx = line_number - 1  # 1-based -> 0-based index
            start_idx = max(0, line_idx - CONTEXT_WINDOW)  # [start_idx, end_idx)
            end_idx = min(len(original_lines), line_idx + CONTEXT_WINDOW + 1)
            assert 0 <= line_idx < len(original_lines)
            assert 0 <= start_idx < end_idx <= len(original_lines)
            assert end_idx - start_idx <= CONTEXT_WINDOW * 2 + 1
            context_lines[start_idx:end_idx] = original_lines[start_idx:end_idx]
        for (
            report_id,
            bug_trace_item_id,
            level,
            filename,
            line_number,
            column_number,
            description,
        ) in marks:
            line_idx = line_number - 1  # 1-based -> 0-based index
            context_lines[line_idx] += line_marker_fmt.format(
                report_id=report_id,
                bug_trace_item_id=bug_trace_item_id,
                level=level,
                filename=filename,
                line_number=line_number,
                column_number=column_number,
                description=description,
            )
        context_lines = _collapse_ellipsis(context_lines)
        assert file_path not in file2context
        file2context[file_path] = {
            "file_path": file_path_to_show,
            "context_lines": context_lines,
        }

    context_with_bug_paths_lines = []
    for i, (abs_file_path, context) in enumerate(file2context.items(), start=1):
        file_path_to_show = context["file_path"]
        context_lines = context["context_lines"]
        lang = _detect_PL_from_file_suffix(abs_file_path) or ""
        context_with_bug_paths_lines.append(f"({i}) File {file_path_to_show}:")
        context_with_bug_paths_lines.append(f"```{lang}")
        context_with_bug_paths_lines.extend(context_lines)
        context_with_bug_paths_lines.append(f"```")
        context_with_bug_paths_lines.append("")
    context_with_bug_paths = "\n".join(context_with_bug_paths_lines)
    del context_with_bug_paths_lines

    json_report = json.dumps(jsons, indent=2)
    text_report = "\n\n\n".join(
        f"{idx}.\n{t.strip()}" for idx, t in enumerate(texts, start=1)
    )
    text_report_section = (
        f"""\
#### Text Report
```text
{text_report}
```"""
        if texts
        else ""
    )

    all_bug_types = _get_facebook_infer_checker_details()
    extended_bug_types = {**bug_types}
    for bug_type in bug_types.keys():
        # only link 1 level
        desc = all_bug_types.get(bug_type, {}).get("description", "No description")
        links = _extract_see_links(desc)
        assert len(links) <= 1
        if links:
            link_name, _ = links[0]
            if link_name in all_bug_types and link_name not in extended_bug_types:
                extended_bug_types[link_name] = []
    bug_type_details_lines = []
    for i, (name, used_report_ids) in enumerate(extended_bug_types.items(), start=1):
        bug_type_details_lines.append(
            f"({i}) {name}: (reported by {', '.join(used_report_ids)})"
        )
        bug_type_details_lines.append(
            f"{all_bug_types.get(name, {}).get('description', 'No description')}"
        )
        bug_type_details_lines.append("")
    bug_type_details = "\n".join(bug_type_details_lines)
    del bug_type_details_lines

    return f"""\
### Analysis Report from Facebook Infer

#### JSON Report
```json
{json_report}
```

{text_report_section}

#### Context Annotated with Bug Trace

**NOTE**:
[1] Only key lines are shown; other lines are replaced with `...`.
[2] The line markers in the context annotated with bug trace are as follows:
    - `// <-- {{report_id}}, bug_trace_item_id={{bug_trace_item_id}}, level={{level}}, {{filename}}:{{line_number}}:{{column_number}}: {{description}}`: Indicates a bug trace item.

{context_with_bug_paths}

#### Bug Type Details
{bug_type_details}
"""


def _make_enahanced_report_for_cppcheck(
    instance,
    jsons: List[dict],
    texts: List[str],
) -> str:
    return _make_enhanced_report_for_codechecker(
        instance,
        jsons,
        texts,
        tool_name="Cppcheck",
    )
