import os
import sys
import subprocess


def build(actual_compiler):
    argv = sys.argv[1:]

    # Make sanitizer report more accurate
    argv = list(
        filter(lambda x: not x.startswith("-O") and not x.startswith("-g"), argv)
    )
    argv.append("-g")
    argv.append("-O0")

    # Add global header file
    ## Example: AGENT4AVR_GLOBAL_HEADER_FILES=/repo_toolkit/safety_property_assert3.h
    global_headers = os.getenv("AGENT4AVR_GLOBAL_HEADER_FILES")
    if global_headers is not None:
        global_headers = global_headers.split(",")
        for header in global_headers:
            if not os.path.exists(header):
                raise FileNotFoundError(f"Global header file {header} not found")
            argv.append("-include")
            argv.append(header)

    # Add link library
    ## Example: AGENT4AVR_LINK_LIBS=/repo_toolkit/libSafePropAssert3.so
    libs = os.getenv("AGENT4AVR_LINK_LIBS")
    if libs:
        for lib in libs.split(","):
            lib = lib.strip()
            if not lib:
                continue

            lib_dir = os.path.dirname(lib)
            lib_name = os.path.basename(lib)

            if lib_dir:
                argv.append(f"-L{lib_dir}")
                argv.append(f"-Wl,-rpath,{lib_dir}")

            if lib_name.startswith("lib") and lib_name.endswith(".so"):
                argv.append(f"-l{lib_name[3:-3]}")
            else:
                # fallback: simply use the full path
                argv.append(lib)

    if "++" in actual_compiler:
        argv.append("-lstdc++")

    # Execute the compilation command
    ret = subprocess.call([actual_compiler] + argv, env=os.environ)
    sys.exit(ret)
