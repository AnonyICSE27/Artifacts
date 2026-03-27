This directory contains runtime dependencies required by our agent toolkits.

1. Download [runtime.tar.gz](https://mega.nz/file/snMEiRhB#8LUqPgij6rVOxgTCG7gSuK_uQx1vmbkrTowsJ-iZiU0)
2. Extract it here: `tar -xzvf runtime.tar.gz`

The tree of this folder is as follows:
```
.
├── README.md
├── clang+llvm-18.1.8-x86_64-linux-gnu-ubuntu-18.04.tar.xz # support clang static analyzer
├── clang_static_analyzer.tar.gz # just used to generate compile database, can be replaced with bear
├── csa_utils.sh # utils for gen_compile_db.sh
├── gen_compile_db.sh # generate compile database
├── libtinfo.so.5.9 # required by clang static analyzer
├── lsp_server.tar.gz # support the code symbol analysis toolkit
└── py-libclang-18.1.1.tar.gz # support code element parsing
```
