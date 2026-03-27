import os
import pprint

THIS_DIR = os.path.dirname(os.path.abspath(__file__))

compilers = ["gcc", "g++", "clang", "clang++"]

# find original compiler path
compiler_paths = {}
for compiler in compilers:
    path = os.popen(f"which {compiler}").read().strip()
    if path and os.path.isfile(path):
        compiler_paths[compiler] = path
print("Original compiler paths:")
pprint.pprint(compiler_paths)
print()

# backup original compiler to .actual
for compiler in compilers:
    original_path = compiler_paths[compiler]
    actual_path = f"{compiler_paths[compiler]}.actual"
    assert not os.path.exists(actual_path)
    print(f"Backup actual compiler {original_path} to {actual_path}")
    os.rename(original_path, actual_path)
    del compiler, actual_path
print()

# create fake compiler
for compiler in compilers:
    target_path = compiler_paths[compiler]
    actual_path = f"{target_path}.actual"
    assert not os.path.exists(target_path)
    assert os.path.exists(actual_path)
    with open(target_path, "w") as fp:
        fp.write(f"#!/usr/bin/env python\n")
        fp.write(f"\n")
        fp.write(f"import sys\n")
        fp.write(f"sys.path.append('{THIS_DIR}')\n")
        fp.write(f"\n")
        fp.write(f"import _compiler\n")
        fp.write(f"\n")
        fp.write(f"if __name__ == '__main__':\n")
        fp.write(f"    _compiler.build('{actual_path}')\n")
    os.system(f"chmod +x {target_path}")
    print(f"Create fake compiler {target_path}")
    del compiler, target_path, actual_path
