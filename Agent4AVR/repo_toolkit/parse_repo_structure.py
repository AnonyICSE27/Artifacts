# NOTE: Run in Instance Docker; Do NOT use third-party librairies

import os
import os
import glob
import json
from typing import Any, List, Set, Dict


def _collect_files_with_suffixes(root_path: str, suffixes: List[str]) -> List[str]:
    assert all(s for s in suffixes)  # suffixes must not be empty
    assert os.path.isdir(root_path)  # root_path must be a directory
    matches: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        for name in filenames:
            if any(name.endswith(suf) for suf in suffixes):
                full_path = os.path.join(dirpath, name)
                matches.append(full_path)
    return matches


def _get_PL_file_suffixes(*lang) -> Set[str]:
    _PL_suffix_table = {
        "c": {".c", ".h"},
        "cpp": {".cpp", ".cc", ".cxx", ".C", ".hpp", ".hh", ".hxx", ".H"},
    }
    suffixes = set()
    for lang in lang:
        suffixes |= _PL_suffix_table[lang]
    return suffixes


def _generate_directory_tree(path: str, suffixes: List[str]) -> str:
    """
    Looks like:
        Agent4AVR/
        ├── __init__.py
        ├── __main__.py
        ├── agents/
        │   ├── __init__.py
        │   ├── agent.py
        │   ├── patch_gen_agent.py
        │   ├── prompts.py
        │   ├── simple_edit_loc_agent.py
        │   ├── simple_patch_gen_agent.py
        │   └── vul_repair_agent.py
        ├── basic.py
        ├── instance.py
        ├── main.py
        ├── model.py
        ├── repo_toolkit/
        │   └── parse_repo_structure.py
        ├── rs_utils.py
        ├── utils.py
        └── vector_store.py
    """

    base_name = os.path.basename(os.path.abspath(path))
    output_lines = []

    def _should_include_dir(dir_path):
        try:
            for entry in os.listdir(dir_path):
                full_path = os.path.join(dir_path, entry)
                if os.path.isdir(full_path) and not os.path.islink(full_path):
                    if _should_include_dir(full_path):
                        return True
                elif os.path.isfile(full_path) or os.path.islink(full_path):
                    if any(entry.endswith(suf) for suf in suffixes):
                        return True
        except Exception:
            pass
        return False

    def _traverse_tree(current_path, level, prefix=""):
        try:
            entries = sorted(os.listdir(current_path))
        except Exception:
            return

        for i, entry in enumerate(entries):
            full_path = os.path.join(current_path, entry)
            is_last = i == len(entries) - 1

            if os.path.islink(full_path):
                try:
                    target = os.readlink(full_path)
                    if any(entry.endswith(suf) for suf in suffixes):
                        output_lines.append(
                            f"{prefix}{'└── ' if is_last else '├── '}{entry} -> {target}"
                        )
                except Exception:
                    pass
                continue

            if os.path.isfile(full_path):
                if any(entry.endswith(suf) for suf in suffixes):
                    output_lines.append(
                        f"{prefix}{'└── ' if is_last else '├── '}{entry}"
                    )
                continue

            if os.path.isdir(full_path):
                if not _should_include_dir(full_path):
                    continue
                output_lines.append(f"{prefix}{'└── ' if is_last else '├── '}{entry}/")
                new_prefix = prefix + ("    " if is_last else "│   ")
                _traverse_tree(full_path, level + 1, new_prefix)

    if not _should_include_dir(path):
        return ""

    output_lines.append(base_name + "/")
    _traverse_tree(path, 0)
    return "\n".join(output_lines)


def parse_repo_structure(repo_path: str) -> Dict[str, Any]:
    # Return: {
    ##      "description": ...,
    ##      "files": [...], # code file rel paths
    ## }

    files = []
    suffixes = _get_PL_file_suffixes("c", "cpp")  # NOTE: only for C/C++
    files += _collect_files_with_suffixes(repo_path, suffixes)
    files = [os.path.relpath(f, repo_path) for f in files]

    tree = _generate_directory_tree(repo_path, suffixes)

    return {
        "description": tree,
        "files": files,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_dir", type=str, required=True)
    args = parser.parse_args()

    if not os.path.isdir(args.repo_dir):
        raise ValueError(f"Repo dir {args.repo_dir} does not exist")

    repo_structure = parse_repo_structure(args.repo_dir)
    print(json.dumps(repo_structure))
