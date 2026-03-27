# NOTE: Run in Instance Docker; ONLY USE a third-party library clang

import io
import os
import sys
import json
import argparse
import traceback
from clang import cindex
from typing import Any, Dict, List, Optional

_stderr_buffer = io.StringIO()
sys.stderr = _stderr_buffer
_log_print = lambda *args, **kwargs: print(*args, **kwargs, file=sys.stderr)

# Map clang CursorKinds to element types
KIND_MAP = {
    cindex.CursorKind.CLASS_DECL: "class",
    cindex.CursorKind.CLASS_TEMPLATE: "class",  # template struct will be "class", but ok here
    cindex.CursorKind.STRUCT_DECL: "struct",
    cindex.CursorKind.UNION_DECL: "union",
    cindex.CursorKind.ENUM_DECL: "enum",
    cindex.CursorKind.FUNCTION_DECL: "function",
    cindex.CursorKind.FUNCTION_TEMPLATE: "function",
    cindex.CursorKind.MACRO_DEFINITION: "macro",
    cindex.CursorKind.VAR_DECL: "global_variable",
    cindex.CursorKind.TYPEDEF_DECL: "struct",
}


def _read_txt_file(file_path: str) -> str:
    _encodings = [
        "utf-8",
        "utf-16",
        "iso-8859-1",
        "windows-1252",
        "ascii",
        "latin-1",
        "utf-8-sig",
        "gbk",
        "utf-16-le",
        "utf-16-be",
    ]
    for encoding in _encodings:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return f.read()
        except (FileNotFoundError, IsADirectoryError) as ex:
            raise ex
        except Exception as ex:
            _log_print(f"[WARN] Failed to read {file_path} with encoding {encoding}")
            _log_print(f"[WARN] Retry with next encoding ...")
    raise ValueError(
        f"Cannot decode file {file_path} with any of the supported encodings\n"
        + f"Supported encodings: {_encodings}"
    )


def _are_paths_equal(path1: str, path2: str) -> bool:
    import os

    norm_path1 = os.path.normcase(os.path.normpath(path1))
    norm_path2 = os.path.normcase(os.path.normpath(path2))

    return norm_path1 == norm_path2


def _collapse_ellipsis(lst: List[str]) -> List[str]:
    result: List[str] = []
    prev = None
    for item in lst:
        if item.strip() == "..." and (prev and prev.strip() == "..."):
            continue
        result.append(item)
        prev = item
    return result


def _get_extent_range(extent: cindex.SourceRange) -> Dict[str, int]:
    return {
        "start_line": extent.start.line,  # 1-based
        "start_column": extent.start.column,  # 1-based
        "end_line": extent.end.line,  # 1-based
        "end_column": extent.end.column,  # 1-based
    }


def _make_element(
    cursor: cindex.Cursor,
    code: str,
    file_path: str,
    parent_path: Optional[str] = None,
) -> Dict[str, Any]:
    name = cursor.spelling or cursor.displayname or f"anonymous_{cursor.hash}"
    path = f"{parent_path}::{name}" if parent_path else name
    abs_path = f"{file_path}::{path}"
    code_segment = code[cursor.extent.start.offset : cursor.extent.end.offset]
    body_range = None
    # For functions or types with a body
    if cursor.kind in (
        cindex.CursorKind.FUNCTION_DECL,
        cindex.CursorKind.CXX_METHOD,
        cindex.CursorKind.FUNCTION_TEMPLATE,
        cindex.CursorKind.CONSTRUCTOR,
        cindex.CursorKind.DESTRUCTOR,
        cindex.CursorKind.CLASS_DECL,
        cindex.CursorKind.CLASS_TEMPLATE,
        cindex.CursorKind.STRUCT_DECL,
        cindex.CursorKind.UNION_DECL,
        cindex.CursorKind.ENUM_DECL,
    ):
        body = None
        for c in cursor.get_children():
            if (
                c.extent.start.offset >= cursor.extent.start.offset
                and c.extent.end.offset <= cursor.extent.end.offset
            ):
                if c.kind == cindex.CursorKind.COMPOUND_STMT:
                    body = c.extent
                    break
        if body:
            body_range = _get_extent_range(body)

    _kind_map = {
        cindex.CursorKind.CXX_METHOD: "function",
        cindex.CursorKind.FUNCTION_TEMPLATE: "function",
        cindex.CursorKind.CONSTRUCTOR: "function",
        cindex.CursorKind.DESTRUCTOR: "function",
        **KIND_MAP,
    }

    return {
        "type": _kind_map[cursor.kind],
        "name": name,
        "path": path,
        "abs_path": abs_path,
        "code": code_segment,
        "range": _get_extent_range(cursor.extent),
        "body_range": body_range,
        "children": [],
    }


def _collect_elements(
    cursor: cindex.Cursor, code: str, file_path: str, parent_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []
    for c in cursor.get_children():
        if c.location.file and c.location.file.name == file_path:
            if c.kind in KIND_MAP:
                elem = _make_element(c, code, file_path, parent_path)
                if c.kind in (
                    cindex.CursorKind.CLASS_DECL,
                    cindex.CursorKind.STRUCT_DECL,
                    cindex.CursorKind.CLASS_TEMPLATE,
                ):
                    for m in c.get_children():
                        if m.kind in (
                            cindex.CursorKind.CXX_METHOD,
                            cindex.CursorKind.FUNCTION_DECL,
                            cindex.CursorKind.FUNCTION_TEMPLATE,
                            cindex.CursorKind.CONSTRUCTOR,
                            cindex.CursorKind.DESTRUCTOR,
                        ):
                            child_elem = _make_element(m, code, file_path, elem["path"])
                            elem["children"].append(child_elem)
                elements.append(elem)
            elif c.kind in (
                cindex.CursorKind.CXX_METHOD,
                cindex.CursorKind.CONSTRUCTOR,
                cindex.CursorKind.DESTRUCTOR,
            ):
                elem = _make_element(c, code, file_path, parent_path)
                elements.append(elem)
            if c.kind == cindex.CursorKind.NAMESPACE:
                ns_path = f"{parent_path}::{c.spelling}" if parent_path else c.spelling
                elements.extend(_collect_elements(c, code, file_path, ns_path))
    return elements


def _parse_cpp_code_structure_impl(
    code: str,
    file_path: str,
    args: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Parse C/C++ code in memory and extract its structural elements using libclang.
    """

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

    index = cindex.Index.create()
    unsaved_files = [(file_path, code)]
    tu = index.parse(
        path=file_path,  # libclang will find the file in args
        args=args,
        unsaved_files=unsaved_files,
        options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
    )
    for diag in tu.diagnostics:
        _log_print(f">>>>>> Diagnosis: {diag.severity}: {diag.spelling}")

    elements = _collect_elements(tu.cursor, code, file_path)
    element_paths = []
    for e in elements:
        element_paths.append(e["path"])
        for chid in e["children"]:
            element_paths.append(chid["path"])

    # Make Skeleton (Find all functions and replace their bodys with "..."
    # TODO: Consider maro function if needed
    def _collect_all_function_elements(elts: List[dict] | None) -> List[dict]:
        _elements = []
        for elt in elts or []:
            if elt["type"] == "function":
                _elements.append(elt)
            else:
                _elements.extend(_collect_all_function_elements(elt["children"]))
        return _elements

    skeleton_lines = code.split("\n")
    function_elements = _collect_all_function_elements(elements)
    for e in function_elements:
        if e["type"] == "function" and e["body_range"]:
            br = e["body_range"]
            br_start_line = br["start_line"]
            br_end_line = br["end_line"]
            start_l_idx = (br_start_line + 1) - 1  # 1-based -> ignore self -> 0-based
            end_l_idx = (br_end_line - 1) - 1  # 1-based -> ignore self -> 0-based
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


def _parse_cpp_code_structure(
    code: str, file_path: str, abs_file_path: str, compile_db: List[dict]
) -> Dict[str, Any]:
    command = None
    for entry in compile_db:
        cmd_file = entry["file"]
        cmd_dir = entry["directory"]
        cmd_filepath = cmd_file
        if not os.path.isabs(cmd_filepath):
            cmd_filepath = f"{cmd_dir}/{cmd_file}"
        if _are_paths_equal(cmd_filepath, abs_file_path):
            command = entry.get("command") or entry.get("arguments") or None
            break

    if not command:
        _log_print(f"File {file_path} not found in compile db")

    if command:

        if isinstance(command, list):
            compile_args = command.copy()
        elif isinstance(command, str):
            compile_args = command.split()
        else:
            raise ValueError(f"Invalid compile command {command} for file {file_path}")

        _log_print(f">>>>>> Original compile args: {compile_args}")

        # Remove the first element, which is the compiler
        if not any(cc in compile_args[0] for cc in ["clang", "clang++", "gcc", "g++"]):
            raise ValueError(f"Invalid compiler {compile_args[0]} for file {file_path}")
        compile_args = compile_args[1:]

        # Remove the file path from compile args, and the "-c" option if it exists
        in_cmd_file = None
        if cmd_file in compile_args:
            in_cmd_file = cmd_file
        else:
            cmd_filename = os.path.basename(cmd_file)
            if cmd_filename in [os.path.basename(a) for a in compile_args]:
                in_cmd_file = [
                    a for a in compile_args if os.path.basename(a) == cmd_filename
                ][0]
            else:
                raise ValueError(
                    f"File {file_path} not found in compile args {compile_args}"
                )
        assert in_cmd_file is not None
        compile_args.remove(in_cmd_file)
        if "-c" in compile_args:
            compile_args.remove("-c")
    else:
        compile_args = []

    try:
        _log_print(f">>>>>> Fixed compile args: {compile_args}")
        return _parse_cpp_code_structure_impl(code, file_path, compile_args)
    except Exception as ex:
        _log_print(f">>>>>> Failed to parse file {file_path} with error: {ex}")
        _PL_suffix_table = {
            "c": {".c", ".h"},
            "cpp": {".cpp", ".cc", ".cxx", ".C", ".hpp", ".hh", ".hxx", ".H"},
        }
        _PL_all_suffixes = set()
        for suffixes in _PL_suffix_table.values():
            _PL_all_suffixes.update(suffixes)
        _fixed_compile_args = []
        for a in compile_args:
            if any(a.endswith(suffix) for suffix in _PL_all_suffixes):
                continue
            _fixed_compile_args.append(a)
        _log_print(f">>>>>> Re-fix compile args: {_fixed_compile_args}")
        return _parse_cpp_code_structure_impl(code, file_path, _fixed_compile_args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_dir", type=str, required=True)
    parser.add_argument("--files", type=str, nargs="+", required=True)
    parser.add_argument("--pretty", action="store_true", default=False)
    args = parser.parse_args()

    args.repo_dir = os.path.abspath(args.repo_dir)
    if not os.path.isdir(args.repo_dir):
        raise ValueError(f"Repo dir {args.repo_dir} does not exist")

    if len(set(args.files)) != len(args.files):
        raise ValueError("Files must be unique")

    for fn in args.files:
        if os.path.isabs(fn):
            raise ValueError(f"File {fn} must be relative path")

    compile_db_fn = os.path.join(args.repo_dir, "compile_commands.json")
    if not os.path.isfile(compile_db_fn):
        gen_compile_db = (
            f"bash /static_analysis_tools/gen_compile_db.sh {args.repo_dir}"
        )
        gen_compile_db += " 1>&2"  # redirect stdout to stderr
        _log_print(f"Running command: {gen_compile_db} ...")
        if 0 != os.system(gen_compile_db):
            raise ValueError(f"Failed to generate compile db for repo {args.repo_dir}")
    if not os.path.isfile(compile_db_fn):
        raise ValueError(f"Compile db file {compile_db_fn} does not exist")

    _log_print(f"Loading compile db from {compile_db_fn} ...")
    with open(compile_db_fn, "r") as f:
        compile_db = json.load(f)

    if "njs" in args.repo_dir:
        _log_print(f"[!!!] Patching compile db for njs files ...")
        for file in args.files:
            if file.lower().endswith(".h"):
                compile_db.append(
                    {
                        "directory": args.repo_dir,
                        "file": file,
                        "command": f"/path/to/clang -c {file} -Dnjs_inline=",
                    }
                )

    _log_print(f"Parsing {len(args.files)} files ...")
    result = {}
    for fn in args.files:
        _log_print(f">>>> Parsing file {fn} ...")
        file_path = os.path.join(args.repo_dir, fn)
        if not os.path.isfile(file_path):
            raise ValueError(f"File {file_path} does not exist")
        code = _read_txt_file(file_path)
        r_dict = _parse_cpp_code_structure(code, fn, file_path, compile_db)
        result[fn] = {"path": fn, "code": code, **r_dict}
    print(json.dumps(result) if not args.pretty else json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        traceback.print_exc(file=sys.stderr)
        print(f"Error: {ex}")
        print(f"stderr: `{_stderr_buffer.getvalue()}`")
        exit(1)
