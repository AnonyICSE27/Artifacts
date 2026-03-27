import os
import pprint

THIS_DIR = os.path.dirname(os.path.abspath(__file__))

compilers = ["gcc", "g++", "clang", "clang++"]

# find compiler paths that have .actual backups
compiler_paths = {}
for compiler in compilers:
    path = os.popen(f"which {compiler}").read().strip()
    if path and os.path.isfile(path):
        actual_path = f"{path}.actual"
        if os.path.exists(actual_path):
            compiler_paths[compiler] = path
print("Compiler paths with .actual backups:")
pprint.pprint(compiler_paths)
print()

# restore original compilers
for compiler in compilers:
    if compiler in compiler_paths:
        fake_path = compiler_paths[compiler]
        actual_path = f"{fake_path}.actual"
        if os.path.exists(actual_path):
            # remove fake compiler
            if os.path.exists(fake_path):
                os.remove(fake_path)
                print(f"Removed fake compiler {fake_path}")
            
            # restore original compiler
            os.rename(actual_path, fake_path)
            print(f"Restored original compiler {fake_path}")
        else:
            print(f"Warning: No .actual backup found for {compiler}")
    else:
        print(f"Warning: Compiler {compiler} not found or no backup exists")
print()

print("Uninitialization completed successfully")