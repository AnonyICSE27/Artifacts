import os
import json
import shlex
import shutil
import atexit
import docker
import asyncio
import tarfile
import tempfile
from pydantic import BaseModel
from dataclasses import dataclass, fields
from typing import Optional, Literal, List, Dict, Any
from contextlib import contextmanager, ExitStack
from swerex.exceptions import CommandTimeoutError
from swerex.deployment.config import (
    LocalDeploymentConfig,
    DockerDeploymentConfig,
    get_deployment,
)
from swerex.deployment.abstract import AbstractDeployment
from swerex.runtime.abstract import (
    BashAction,
    CreateBashSessionRequest,
    ReadFileRequest,
    WriteFileRequest,
    UploadRequest,
)
from . import rs_utils as rsu
from .basic import Patch
from .utils import (
    _dlog,
    _get_uuid,
    _generate_sha256_key,
    _extract_sanitizer_report,
    _parse_git_diff,
)


SECB_IMAGE_PREFIX = "hwiwonlee/secb.eval.x86_64"

# For experimental platform in which docker is not available
USE_LOCAL_DEPLOYMENT = os.getenv("USE_LOCAL_DEPLOYMENT", "0") == "1"
LOCAL_IS_RESTRICTED = os.getenv("LOCAL_IS_RESTRICTED", "0") == "1"


class PRootLocalDeploymentConfig(BaseModel):
    """Configuration for running locally with proot."""

    rootfs_tar: str = ""
    """The path to the rootfs tarball."""
    rootfs_dir: str = ""
    """The path to the rootfs directory. (LAZY-INIT)"""
    bashrc_path: str = ""
    """The path to the bashrc file. (LAZY-INIT)"""
    proot_args: list[str] = []
    """The arguments to pass to proot except '-r <rootfs_dir>'."""
    deployment_config: LocalDeploymentConfig = None
    """The LocalDeploymentConfig"""

    def model_post_init(self, __context):
        if self.deployment_config is None:
            self.deployment_config = LocalDeploymentConfig()

    def get_deployment(self) -> AbstractDeployment:
        PROOT_ROOTFS_TEMP_DIR = os.getenv("PROOT_ROOTFS_TEMP_DIR", "")
        if not PROOT_ROOTFS_TEMP_DIR or not os.path.isdir(PROOT_ROOTFS_TEMP_DIR):
            raise ValueError("PROOT_ROOTFS_TEMP_DIR not set & created")

        if not os.path.isfile(self.rootfs_tar):
            raise FileNotFoundError(f"rootfs tarball not found: {self.rootfs_tar}")
        if not self.rootfs_dir:
            tmpdir = tempfile.mkdtemp(
                prefix=os.path.basename(self.rootfs_tar).replace(".tar.gz", "-"),
                dir=PROOT_ROOTFS_TEMP_DIR,
            )
            self.rootfs_dir = tmpdir

            def _cleanup_rootfs_dir():
                if os.path.isdir(tmpdir):
                    _dlog.i(f"Cleaning up rootfs dir: {tmpdir}")
                    shutil.rmtree(tmpdir)

            atexit.register(_cleanup_rootfs_dir)  # ..., but enough

        _dlog.i(f"Created rootfs dir: {self.rootfs_dir}")
        _dlog.i(f"Extracting rootfs tarball {self.rootfs_tar} ...")
        with tarfile.open(self.rootfs_tar, "r:*") as tf:
            tf.extractall(path=self.rootfs_dir)
        _dlog.i(f"Extracted rootfs tarball to {self.rootfs_dir}")

        _dlog.i(f"Patching .bashrc ...")
        bashrc_path = f"{self.rootfs_dir}/root/.bashrc"
        with open(bashrc_path, "a") as fp:
            fp.write("\n\n# PRootLocalDeployment\n")
            fp.write("export PS1=SHELLPS1PREFIX\n")
            fp.write("export PS2=\n")
            fp.write("export PS0=\n\n")
        self.bashrc_path = bashrc_path
        _dlog.i(f"Patched .bashrc: {bashrc_path}")

        _dlog.i("Patching /etc/resolv.conf ...")
        shutil.copy("/etc/resolv.conf", f"{self.rootfs_dir}/etc/resolv.conf")
        _dlog.i(f"Patched {self.rootfs_dir}/etc/resolv.conf")

        return self.deployment_config.get_deployment()


DeploymentConfig = DockerDeploymentConfig | PRootLocalDeploymentConfig


@dataclass
class CheckoutResult:
    success: bool
    message: str


@dataclass
class BuildResult:
    success: bool
    timeout: bool
    exit_code: Optional[int]
    raw_output: str
    extra_info: Dict[str, Any] = None


@dataclass
class ReproResult:
    timeout: bool
    sanitizer_triggered: bool
    sanitizer_report: Optional[str]
    raw_output: str
    _exit_code: int
    extra_info: Dict[str, Any] = None


@dataclass
class TestResult:
    passed: bool
    timeout: bool
    exit_code: int
    raw_output: str
    extra_info: Dict[str, Any] = None


@dataclass
class InstanceConfig:
    """SEC-Bench Instance Config"""

    REPO_TOOLKIT_DIR = "/repo_toolkit"
    STATIC_ANALYSIS_TOOLS_DIR = "/static_analysis_tools"

    id: str
    repo: str
    project_name: str
    lang: str
    work_dir: str
    sanitizer: str
    bug_description: str
    base_commit: str
    build_sh: str
    secb_sh: str
    dockerfile: str
    patch: str
    exit_code: str
    sanitizer_report: str
    bug_report: str
    deployment_config: DeploymentConfig = None

    def __post_init__(self):
        this_dir = os.path.abspath(os.path.dirname(__file__))
        repo_toolkit_dir = os.path.join(this_dir, "repo_toolkit")
        assert os.path.isdir(repo_toolkit_dir)

        STATIC_ANALYSIS_TOOLS_DIR = os.getenv("STATIC_ANALYSIS_TOOLS_DIR")
        if not (STATIC_ANALYSIS_TOOLS_DIR and os.path.isdir(STATIC_ANALYSIS_TOOLS_DIR)):
            raise ValueError("STATIC_ANALYSIS_TOOLS_DIR not set & exists")

        PROOT_ROOTFS_TARS_DIR = os.getenv("PROOT_ROOTFS_TARS_DIR")
        if USE_LOCAL_DEPLOYMENT and (
            not PROOT_ROOTFS_TARS_DIR or not os.path.isdir(PROOT_ROOTFS_TARS_DIR)
        ):
            raise ValueError("PROOT_ROOTFS_TARS_DIR not set & exists")

        # codeql_homes = _get_codeql_homes()
        # codeql_cli_home = codeql_homes["codeql_cli_home"]
        # codeql_repo_home = codeql_homes["codeql_repo_home"]
        # codeql_cache_dir = codeql_homes["codeql_cache_dir"]
        # assert os.path.isdir(codeql_cli_home)
        # assert os.path.isdir(codeql_repo_home)
        # assert os.path.isdir(codeql_cache_dir)

        if not USE_LOCAL_DEPLOYMENT:
            iid = self.id
            image_name = f"{SECB_IMAGE_PREFIX}.{iid}:patch"
            self.deployment_config = DockerDeploymentConfig(
                image=image_name,
                python_standalone_dir=None,
                docker_args=[
                    "-v",
                    f"{repo_toolkit_dir}:{self.REPO_TOOLKIT_DIR}:ro",
                    "-v",
                    f"{STATIC_ANALYSIS_TOOLS_DIR}:{self.STATIC_ANALYSIS_TOOLS_DIR}:ro",
                    # "-v",
                    # f"{codeql_cli_home}:{self.CODEQL_CLI_HOME}:ro",
                    # "-v",
                    # f"{codeql_repo_home}:{self.CODEQL_REPO_HOME}:ro",
                    # "-v",
                    # f"{codeql_cache_dir}:{self.CODEQL_CACHE_DIR}:rw",
                    "--security-opt",
                    "seccomp=unconfined",
                    # "--memory=16g",
                    # "--cpus=8",
                    # "--cpuset-cpus=0-13",
                ],
                startup_timeout=60 * 10,  # 10 min
            )
            del iid, image_name
        else:
            # hwiwonlee_secb.eval.x86_64.njs.cve-2022-32414_patch.rootfs.tar.gz
            self.deployment_config = PRootLocalDeploymentConfig(
                rootfs_tar=f"{PROOT_ROOTFS_TARS_DIR}/hwiwonlee_secb.eval.x86_64.{self.id}_patch.rootfs.tar.gz",
                rootfs_dir="",  # lazy-init in get_deployment()
                proot_args=[
                    # proot -r ./rootfs/ -0 -w / -b /dev:/dev -b /proc:/proc -b /sys:/sys /bin/bash --rcfile /root/.bashrc
                    "-0",
                    "-w",
                    self.work_dir,
                    "-b",
                    "/dev:/dev",
                    "-b",
                    "/proc:/proc",
                    "-b",
                    "/sys:/sys",
                    "-b",
                    f"{repo_toolkit_dir}:{self.REPO_TOOLKIT_DIR}",
                    "-b",
                    f"{STATIC_ANALYSIS_TOOLS_DIR}:{self.STATIC_ANALYSIS_TOOLS_DIR}",
                ],
            )

    def to_dict(self) -> dict:
        result = self.__dict__.copy()
        result["deployment_config"] = self.deployment_config.model_dump()
        return result


@dataclass
class Instance:
    """SEC-Bench Instance"""

    DEFAULT_CODEQL_DB_PATH = "/codeql_db"

    config: InstanceConfig
    deployment: Optional[AbstractDeployment] = None
    id: str = None
    __started: bool = False

    @property
    def repo_path(self) -> str:
        return self.config.work_dir

    @property
    def repo_parent_dir(self) -> str:
        return os.path.dirname(self.repo_path)

    @property
    def repo_name(self) -> str:
        assert self.config.project_name == os.path.basename(self.config.work_dir)
        return self.config.project_name

    def __post_init__(self):
        self.id = f"{self.config.id}@{_generate_sha256_key(self.config)}"

    def __enter__(self):
        if self.__started:
            raise RuntimeError("Instance already started")

        rsu._ilog(f"Starting instance {self.id} ...")

        MAX_TRIES = 10
        for _ in range(MAX_TRIES):
            try:
                self.deployment = get_deployment(self.config.deployment_config)
                asyncio.run(self.deployment.start())
                break
            except Exception as ex:
                if "ports are not available" not in str(ex):
                    raise ex
                rsu._wlog("Failed to start deployment, retry ...")
                rsu._wlog(">>>> ports are not available")
        else:
            raise RuntimeError(f"Failed to start deployment after {MAX_TRIES} retries")

        if not USE_LOCAL_DEPLOYMENT:
            asyncio.run(
                self.deployment.runtime.create_session(
                    CreateBashSessionRequest(startup_source=["/root/.bashrc"])
                )
            )
        else:
            asyncio.run(
                self.deployment.runtime.create_session(
                    CreateBashSessionRequest(startup_timeout=30)
                )
            )
            asyncio.run(
                self.deployment.runtime.run_in_session(
                    BashAction(
                        command=f"env -i proot -r {self.config.deployment_config.rootfs_dir} {' '.join(self.config.deployment_config.proot_args)} /bin/bash --rcfile /{os.path.relpath(self.config.deployment_config.bashrc_path, self.config.deployment_config.rootfs_dir)}",
                        is_interactive_command=True,
                    )
                )
            )
        self.set_env_variables({"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})

        # check network connection
        self.communicate(
            "curl -I github.com",
            timeout=30,
            check="raise",
            error_msg="Failed to curl github.com",
        )

        patch_git_cmds = [
            "git config --global protocol.file.allow always",
            'git config --global --add safe.directory "*"',
        ]
        for cmd in patch_git_cmds:
            self.communicate(
                cmd,
                timeout=None,
                check="raise",
                error_msg=f"Failed to run git command: {cmd}",
            )

        init_git_author_info_cmds = [
            f"pushd {self.repo_path} 1>/dev/null 2>/dev/null",
            'git config --global user.email "agent4avr@apr.com"',
            'git config --global user.name "agent4avr"',
            "popd 1>/dev/null 2>/dev/null",
        ]
        self.communicate(
            " && ".join(init_git_author_info_cmds),
            timeout=None,
            check="raise",
            error_msg=f"Failed to init git author info",
        )

        # NOTE: Set env variables, VERY IMPORTANT
        if not USE_LOCAL_DEPLOYMENT:
            client = __import__("docker").from_env()
            image = client.images.get(self.config.deployment_config.image)  # type: ignore
            env_attrs = image.attrs.get("Config", {}).get("Env", [])
            env_dict = dict(item.split("=", 1) for item in env_attrs if "=" in item)
            self.set_env_variables(env_dict)
            del client, image, env_attrs, env_dict
        else:
            env_attrs = [
                "HOME=/root",  # explicitly set HOME
                "PATH=/usr/local/ssl/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/out",
                "DEBIAN_FRONTEND=noninteractive",
                "OUT=/out",
                "SRC=/src",
                "WORK=/work",
                "HWASAN_OPTIONS=random_tags=0",
                "FUZZINTRO_OUTDIR=/src",
                "CMAKE_VERSION=3.29.2",
                "CC=clang",
                "CXX=clang++",
                "CCC=clang++",
                "CFLAGS=-w -Wno-yacc -Wno-incompatible-pointer-types",
                "CXXFLAGS_EXTRA=-stdlib=libc++",
                "CXXFLAGS=-w -Wno-yacc -Wno-incompatible-pointer-types",
                "PYTHON_VERSION=3.10.14",
                "CCACHE_VERSION=4.10.2",
                "BAZELISK_VERSION=1.9.0",
                "SANITIZER_FLAGS_address=-fsanitize=address -fsanitize-address-use-after-scope",
                "SANITIZER_FLAGS_hwaddress=-fsanitize=hwaddress -fuse-ld=lld -Wno-unused-command-line-argument",
                "SANITIZER_FLAGS_undefined=-fsanitize=array-bounds,bool,builtin,enum,function,integer-divide-by-zero,null,object-size,return,returns-nonnull-attribute,shift,signed-integer-overflow,unsigned-integer-overflow,unreachable,vla-bound,vptr -fno-sanitize-recover=array-bounds,bool,builtin,enum,function,integer-divide-by-zero,null,object-size,return,returns-nonnull-attribute,shift,signed-integer-overflow,unreachable,vla-bound,vptr",
                "SANITIZER_FLAGS_undefined_aarch64=-fsanitize=array-bounds,bool,builtin,enum,integer-divide-by-zero,null,object-size,return,returns-nonnull-attribute,shift,signed-integer-overflow,unsigned-integer-overflow,unreachable,vla-bound,vptr -fno-sanitize-recover=array-bounds,bool,builtin,enum,integer-divide-by-zero,null,object-size,return,returns-nonnull-attribute,shift,signed-integer-overflow,unreachable,vla-bound,vptr",
                "SANITIZER_FLAGS_memory=-fsanitize=memory -fsanitize-memory-track-origins",
                "SANITIZER_FLAGS_thread=-fsanitize=thread",
                "SANITIZER_FLAGS_introspector=-O0 -flto -fno-inline-functions -fuse-ld=gold -Wno-unused-command-line-argument",
                "SANITIZER_FLAGS_coverage=",
                "UBSAN_OPTIONS=silence_unsigned_overflow=1",
                "DFSAN_OPTIONS=warn_unimplemented=0",
                "COVERAGE_FLAGS=-fsanitize=fuzzer-no-link",
                "COVERAGE_FLAGS_coverage=-fprofile-instr-generate -fcoverage-mapping -pthread -Wl,--no-as-needed -Wl,-ldl -Wl,-lm -Wno-unused-command-line-argument",
                f"SANITIZER={self.config.sanitizer}",
                "FUZZING_ENGINE=libfuzzer",
                "ARCHITECTURE=x86_64",
                "LIB_FUZZING_ENGINE_DEPRECATED=/usr/lib/libFuzzingEngine.a",
                "LIB_FUZZING_ENGINE=/usr/lib/libFuzzingEngine.a",
                "FUZZER_LDFLAGS=",
                "CENTIPEDE_BIN_DIR=/src/fuzztest/bazel-bin",
                "CCACHE_DIR=/ccache/cache",
                "CCACHE_COMPILERCHECK=none",
                "CCACHE_COMPILERTYPE=clang",
                "LD_LIBRARY_PATH=/usr/local/ssl/lib",
                f"FUZZING_LANGUAGE={self.config.lang}",
                f"PROJECT_NAME={self.config.repo.split("/")[-1]}",
                "LANG=C.UTF-8",
            ]
            env_dict = dict(item.split("=", 1) for item in env_attrs if "=" in item)
            self.set_env_variables(env_dict)
            del env_attrs, env_dict

        _dlog.i("Patching /src/build.sh ...")
        build_sh_head = f"""\

# Let sanitizer log containing line:col information
# Enable SAFETY_PROPERTY_ASSERT
export CFLAGS="$CFLAGS -g -O0 -include {self.config.REPO_TOOLKIT_DIR}/safety_property_assert.h"
export CXXFLAGS="$CXXFLAGS -g -O0 -include {self.config.REPO_TOOLKIT_DIR}/safety_property_assert.h"

# if [ "${{AGENT4AVR_DISABLE_SANITIZER:-0}}" = "1" ]; then
#     echo "ORIGINAL: CFLAGS=$CFLAGS"
#     CFLAGS="$(python /repo_toolkit/patch_cflags.py "$CFLAGS")"
#     echo "PATCHED:  CFLAGS=$CFLAGS"

#     echo ""
#     echo "ORIGINAL: CXXFLAGS=$CXXFLAGS"
#     CXXFLAGS="$(python /repo_toolkit/patch_cflags.py "$CXXFLAGS")"
#     echo "PATCHED:  CXXFLAGS=$CXXFLAGS"
# fi


"""
        build_sh_path = "/src/build.sh"
        build_sh_lines = self.read_file(build_sh_path).splitlines()
        if build_sh_lines[0].startswith("#!"):
            build_sh_lines[1:1] = build_sh_head.splitlines()
        else:
            build_sh_lines[0:0] = build_sh_head.splitlines()
        self.write_file(build_sh_path, "\n".join(build_sh_lines))
        _dlog.i("Patched /src/build.sh")

        rsu._ilog(f"Started instance {self.id}")
        self.__started = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.__started:
            assert self.deployment is not None
            rsu._ilog(f"Stopping instance {self.id} ...")
            try:
                rsu._ilog(f">>>> image_name: {self.config.deployment_config.image}")
            except:
                pass
            asyncio.run(self.deployment.stop())
            rsu._ilog(f"Stopped instance {self.id}")
            self.deployment = None
            return False
        raise RuntimeError("Instance not started")

    def set_env_variables(self, env_variables: dict[str, str]) -> None:
        """Set environment variables in the environment."""
        if not env_variables:
            _dlog("No env variables to set")
            return
        _env_setters = [
            f"export {k}={shlex.quote(str(v))}" for k, v in env_variables.items()
        ]
        command = " && ".join(_env_setters)
        _dlog(f"Set env variables: `{command}`")
        self.communicate(command, check="raise")

    def communicate(
        self,
        input: str,
        timeout: int | float = 25,
        *,
        check: Literal["warn", "ignore", "raise"] = "ignore",
        error_msg: str = "Command failed",
        quiet: bool = False,
    ) -> str:
        """Executes a command in the running shell. The details of this are handled by
        the SWE-ReX deployment/runtime.

        Args:
            input: input to send to container
            timeout: duration to wait for output
            check: `ignore`: do not extract exit code (more stable), `warn`: extract exit code and log error if
                exit code is non-zero, `raise`: raise error if exit code is non-zero
            error_msg: error message to raise if the command fails

        Returns:
            output: output from container
        """

        if quiet:
            log_fn = lambda *args, **kwargs: None
        else:
            log_fn = _dlog

        log_fn(f"Executing command: {input!r} ...")
        rex_check = "silent" if check else "ignore"
        r = asyncio.run(
            self.deployment.runtime.run_in_session(
                BashAction(command=input, timeout=timeout, check=rex_check)
            )
        )
        output = r.output
        log_fn(f"Output: `{output[:200]}` (only show first 200 characters)")
        if check != "ignore" and r.exit_code != 0:
            log_fn(f"{error_msg}:\n{output}")
            msg = f"Command {input!r} failed ({r.exit_code=}): {error_msg}"
            log_fn(msg)
            if check == "raise":
                raise RuntimeError(msg)
        return output

    def get_head_commit_hash(self) -> str:
        git_rev_parse_cmds = [
            f"pushd {self.repo_path} 1>/dev/null 2>/dev/null",
            "git rev-parse HEAD",
            "popd 1>/dev/null 2>/dev/null",
        ]
        head_commit_hash = self.communicate(
            " && ".join(git_rev_parse_cmds),
            timeout=None,
            check="raise",
            error_msg=f"Failed to get HEAD commit hash",
        ).strip()
        return head_commit_hash

    def reset_to_commit(self, commit_hash: str) -> None:
        git_reset_cmds = [
            f"pushd {self.repo_path}",
            f"git reset --hard {commit_hash}",
            "popd",
        ]
        self.communicate(
            " && ".join(git_reset_cmds),
            timeout=None,
            check="raise",
            error_msg=f"Failed to reset to commit {commit_hash} by `{' && '.join(git_reset_cmds)}`",
        )

    def reset_to_base_commit(self) -> None:
        head_commit_hash = self.get_head_commit_hash()
        if head_commit_hash != self.config.base_commit:
            _dlog(f"Reset instance repo to base commit ...")
            _dlog(f">>>> from: {head_commit_hash}")
            _dlog(f">>>> to: {self.config.base_commit}")
            self.reset_to_commit(self.config.base_commit)
            assert self.get_head_commit_hash() == self.config.base_commit
        del head_commit_hash

    def apply_git_diff(self, git_diff: str, message: str) -> str:
        head_commit = self.get_head_commit_hash()
        # Apply git diff
        git_diff_tmp_file = f"/tmp/{_get_uuid()}_git_diff_to_apply.diff"
        self.write_file(git_diff_tmp_file, git_diff)
        git_apply_cmds = [
            f"pushd {self.repo_path}",
            f"git apply --ignore-whitespace {git_diff_tmp_file}",
            "popd",
        ]
        try:
            self.communicate(
                " && ".join(git_apply_cmds),
                timeout=None,
                check="raise",
                error_msg=f"Failed to apply git diff by `{' && '.join(git_apply_cmds)}`",
            )
        finally:
            self.communicate(
                f"rm -f {git_diff_tmp_file}",
                timeout=None,
                check="warn",
                error_msg=f"Failed to remove temporary git diff file {git_diff_tmp_file}",
            )
        # Commit the changes
        git_diff_details = _parse_git_diff(git_diff)
        changed_files = [d["file"] for d in git_diff_details]
        git_commit_cmds = [
            f"pushd {self.repo_path}",
            f"git add {' '.join(shlex.quote(f) for f in changed_files)}",
            f"git commit -m {shlex.quote(message)}",
            "popd",
        ]

        if self.config.id in ["libsndfile.cve-2018-19432"]:
            # Not good practice but enough
            _dlog.w(f"To-Remove git hooks for {self.config.id}")
            _rm_ghook = f"rm -rf {self.repo_path}/.git/hooks/pre-commit"
            git_commit_cmds[1:1] = [_rm_ghook]
            del _rm_ghook

        self.communicate(
            " && ".join(git_commit_cmds),
            timeout=None,
            check="raise",
            error_msg=f"Failed to commit applied git diff by `{' && '.join(git_commit_cmds)}`",
        )
        # Get new commit hash
        new_commit_hash = self.get_head_commit_hash()
        assert new_commit_hash != head_commit
        return new_commit_hash

    def get_repo_structure(self) -> Dict[str, Any]:
        repo_dir_arg = shlex.quote(self.repo_path)
        return json.loads(
            self.communicate(
                f"python {self.config.REPO_TOOLKIT_DIR}/parse_repo_structure.py --repo_dir {repo_dir_arg}",
                timeout=None,
                check="raise",
                error_msg="Failed to parse repo structure",
            )
        )

    def get_code_structure(self, files: List[str]) -> Dict[str, Any]:  # file => info
        repo_dir_arg = shlex.quote(self.repo_path)
        files_arg = " ".join(shlex.quote(f) for f in files)
        return json.loads(
            self.communicate(
                f"bash {self.config.REPO_TOOLKIT_DIR}/parse_cpp_code_structure.sh --repo_dir {repo_dir_arg} --files {files_arg}",
                timeout=None,
                check="raise",
                error_msg="Failed to parse code structure",
            )
        )

    def read_file(
        self,
        path: str,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str | None:
        """Read file contents from container

        Args:
            path: Absolute path to file
            encoding: Encoding to use when reading the file. None means default encoding.
                This is the same as the `encoding` argument of `Path.read_text()`
            errors: Error handling to use when reading the file. None means default error handling.
                This is the same as the `errors` argument of `Path.read_text()`

        Returns:
            file_contents: Contents of file as string
        """

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

        if encoding:
            _encodings = [encoding]

        if USE_LOCAL_DEPLOYMENT:
            local_abs_path = f"{self.config.deployment_config.rootfs_dir}/{path}"
            _dlog(f"Redirect read path '{path}' to local path '{local_abs_path}'")
            path = local_abs_path
            del local_abs_path

        _dlog(f"[INSTANCE] Reading file: `{path}`")

        last_exp = None
        for enc in _encodings:
            try:
                return asyncio.run(
                    self.deployment.runtime.read_file(
                        ReadFileRequest(path=str(path), encoding=enc, errors=errors)
                    )
                ).content
            except (FileNotFoundError, IsADirectoryError):
                return None
            except Exception as ex:
                _dlog.w(f"Failed to read file {path} with encoding {enc}; Retrying ...")
                _dlog.w(f">>>> Exception: {ex}")
                last_exp = ex
        raise RuntimeError(f"Failed to read {path} with any encoding") from last_exp

    def write_file(self, path: str, content: str) -> None:
        """Write content to file in container"""

        if USE_LOCAL_DEPLOYMENT:
            local_abs_path = f"{self.config.deployment_config.rootfs_dir}/{path}"
            _dlog(f"Redirect write path '{path}' to local path '{local_abs_path}'")
            path = local_abs_path
            del local_abs_path

        _dlog(f"[INSTANCE] Writing file: `{path}`")

        asyncio.run(
            self.deployment.runtime.write_file(
                WriteFileRequest(path=str(path), content=content)
            )
        )

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """Upload file to container"""

        _dlog(f"[INSTANCE] Uploading: `local:{local_path}` to `remote:{remote_path}`")
        asyncio.run(
            self.deployment.runtime.upload(
                UploadRequest(
                    source_path=local_path,
                    target_path=remote_path,
                )
            )
        )

    def apply_patch_without_auto_restore(self, patch: Patch) -> None:
        file_paths = [f"{self.repo_path}/{f}" for f in patch.file_paths]
        file_patches = patch.patched_file_contents
        assert len(file_paths) == len(file_patches)
        for file_path, file_patch in zip(file_paths, file_patches, strict=True):
            self.write_file(file_path, file_patch)

    @contextmanager
    def apply_patch(self, patch: Patch):
        instance = self

        class FilePatcher:
            def __init__(self, abs_file_path, patched_content):
                self.file_path = abs_file_path
                self.patched_content = patched_content

            def __enter__(self):
                # Save the original file
                self.orig_file_content = instance.read_file(self.file_path)
                # Write patched file
                instance.write_file(self.file_path, self.patched_content)
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                # Restore original file
                assert self.orig_file_content is not None
                instance.write_file(self.file_path, self.orig_file_content)
                return False

        file_paths = [f"{self.repo_path}/{f}" for f in patch.file_paths]
        file_patches = patch.patched_file_contents
        file_patchers = [
            FilePatcher(file_path, file_patch)
            for file_path, file_patch in zip(file_paths, file_patches, strict=True)
            if file_patch is not None
        ]

        with ExitStack() as stack:
            for file_patcher in file_patchers:
                stack.enter_context(file_patcher)
            _dlog(f"Applyed patch to: {file_paths}")
            yield
        _dlog(f"Restored files: {file_paths}")

    def build(self, skip_generate_compile_db: bool = False) -> BuildResult:
        if USE_LOCAL_DEPLOYMENT and LOCAL_IS_RESTRICTED:
            raise RuntimeError("Local build is restricted")

        BUILD_TIMEOUT = int(os.environ.get("BUILD_TIMEOUT", 60 * 10))
        NO_TIMEOUT_MSG = "__NO_WSEFTTREDGDD_TIMEOUT__"
        TIMEOUT_EXIT_CODE = 124

        command_list = []

        if not skip_generate_compile_db:
            gen_compile_db_cmd = f"bash {self.config.STATIC_ANALYSIS_TOOLS_DIR}/gen_compile_db.sh {self.repo_path}"
            command_list.append(gen_compile_db_cmd)

        command_list.extend(
            [
                f"secb build 2>&1",
                f"exit_code=$?",
                f"echo {NO_TIMEOUT_MSG}",
                f"exit $exit_code",
            ]
        )
        command = "; ".join(command_list)
        command = f"timeout {BUILD_TIMEOUT+5}s bash -c '{command}'"
        _dlog(f"Building project {self.repo_path} ...")
        _dlog(f">>>> Command: `{command}`")

        r = asyncio.run(
            self.deployment.runtime.run_in_session(
                BashAction(command=command, check="silent")
            )
        )
        timeout = r.exit_code == TIMEOUT_EXIT_CODE and NO_TIMEOUT_MSG not in r.output

        if not timeout:
            assert NO_TIMEOUT_MSG in r.output
            success = "BUILD COMPLETED SUCCESSFULLY!" in r.output
            if not (success or "BUILD FAILED!" in r.output):
                _dlog.w(f"Assert failed; output: `{r.output}`")
            assert success or "BUILD FAILED!" in r.output
            assert (success and r.exit_code == 0) or (not success and r.exit_code != 0)
            exit_code = r.exit_code
            output = r.output
            # print("======================")
            # print(r)
            # print("======================")
            # print(exit_code)
            # print("======================")
            # print(output)
            # print("======================")
            _dlog("Build successfully" if success else "Build Failed")
            if not success:
                _dlog(f">>>> exit code: {exit_code}")
                _dlog(f">>>> ouput: `{output}`")
        else:
            _dlog.w("Timeout while building")
            success = False
            exit_code = -1
            output = f"BUILD TIMEOUT WITH {BUILD_TIMEOUT} SECONDS!"

        assert not (success and timeout)
        return BuildResult(
            success=success,
            timeout=timeout,
            exit_code=exit_code,
            raw_output=output.rstrip().removesuffix(NO_TIMEOUT_MSG),
        )

    def repro(self) -> ReproResult:
        if USE_LOCAL_DEPLOYMENT and LOCAL_IS_RESTRICTED:
            _dlog.w("Local repro is restricted:")
            _dlog.w(">>>> sanitizer-assisted repro is not supported")
            _dlog.w(">>>> sanitizer-triggered never happens")

        REPRO_TIMEOUT = int(os.environ.get("REPRO_TIMEOUT", 60 * 10))
        NO_TIMEOUT_MSG = "__NO_WSEFTTREDGDD_TIMEOUT__"
        TIMEOUT_EXIT_CODE = 124

        command_list = [
            f"secb repro 2>&1",
            f"exit_code=$?",
            f"echo {NO_TIMEOUT_MSG}",
            f"exit $exit_code",
        ]
        command = "; ".join(command_list)
        command = f"timeout {REPRO_TIMEOUT+5}s bash -c '{command}'"
        _dlog(f"Running PoC for {self.config.id} ...")
        _dlog(f">>>> Command: `{command}`")

        r = asyncio.run(
            self.deployment.runtime.run_in_session(
                BashAction(command=command, check="silent")
            )
        )
        timeout = r.exit_code == TIMEOUT_EXIT_CODE and NO_TIMEOUT_MSG not in r.output

        if not timeout:
            assert NO_TIMEOUT_MSG in r.output
            exit_code = r.exit_code
            output = r.output
            sanitizer_report = _extract_sanitizer_report(output)
            _dlog("Sanitizer" + ("" if sanitizer_report else " not") + " triggered")
            _dlog(f">>>> exit code: {exit_code}")
            if len(output.splitlines()) > 100:
                output_to_show = "\n\n...\n\n" + ("\n".join(output.splitlines()[-100:]))
            else:
                output_to_show = output
            _dlog(f">>>> output: `{output_to_show}`")
        else:
            _dlog.w("Timeout while runnning PoC")
            exit_code = -1
            output = f"RUN POC TIMEOUT WITH {REPRO_TIMEOUT} SECONDS!"
            sanitizer_report = f"RUN POC TIMEOUT WITH {REPRO_TIMEOUT} SECONDS!"

        assert not (timeout and not sanitizer_report.startswith("RUN POC TIMEOUT WITH"))
        return ReproResult(
            timeout=timeout,
            sanitizer_triggered=sanitizer_report is not None,
            sanitizer_report=sanitizer_report,
            raw_output=output.rstrip().removesuffix(NO_TIMEOUT_MSG),
            _exit_code=exit_code,
        )

    def test(self) -> TestResult:
        THIS_DIR = os.path.dirname(os.path.abspath(__file__))
        TEST_SCRIPT_DIR = f"{THIS_DIR}/../SEC-bench+/functional_testing_scripts"
        assert os.path.exists(TEST_SCRIPT_DIR)

        def _lookup_test_scripts(instance: InstanceConfig, test_script_dir: str):
            iid = instance.id
            project = instance.project_name
            test_script_actual_dir = None

            test_script_proj_dir = f"{test_script_dir}/{project}"
            if not os.path.isdir(test_script_proj_dir):
                raise FileNotFoundError(f"Test script for `{project}` not found")

            test_script_ins_dir = f"{test_script_proj_dir}/{iid}"
            if os.path.isdir(test_script_ins_dir):
                _dlog.i(f"Found instance-specific test script: `{test_script_ins_dir}`")
                test_script_actual_dir = test_script_ins_dir
            else:
                _dlog.i(f"Use project-specific test script: `{test_script_proj_dir}`")
                test_script_actual_dir = test_script_proj_dir

            test_sh_path = f"{test_script_actual_dir}/test.sh"
            test_py_path = f"{test_script_actual_dir}/test.py"
            artifacts_json_path = f"{test_script_actual_dir}/test_artifacts.json"
            if not (os.path.isfile(test_sh_path) and os.path.isfile(test_py_path)):
                raise FileNotFoundError(f"Test script for `{iid}` not found")

            artifacts = []
            if os.path.isfile(artifacts_json_path):
                with open(artifacts_json_path, "r") as f:
                    artifacts = json.load(f)
                    artifacts = [f"{test_script_actual_dir}/{a}" for a in artifacts]

            return test_sh_path, test_py_path, artifacts

        test_sh_path, test_py_path, artifacts = _lookup_test_scripts(
            instance=self.config,
            test_script_dir=TEST_SCRIPT_DIR,
        )
        assert os.path.isfile(test_sh_path) and os.path.isfile(test_py_path)
        for artifact in artifacts:
            if not os.path.isfile(artifact):
                raise FileNotFoundError(f"Artifact `{artifact}` not found")
        for artifact in artifacts:
            self.upload_file(artifact, f"{self.repo_path}/{os.path.basename(artifact)}")

        # Copy test scripts to instance directory
        remote_test_sh_path = f"{self.repo_path}/{os.path.basename(test_sh_path)}"
        remote_test_py_path = f"{self.repo_path}/{os.path.basename(test_py_path)}"
        self.write_file(remote_test_sh_path, rsu._read_text_file(test_sh_path))
        self.write_file(remote_test_py_path, rsu._read_text_file(test_py_path))
        del remote_test_sh_path
        del test_sh_path, test_py_path

        # Run test.py

        TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", 60 * 60))  # 1 hour
        NO_TIMEOUT_MSG = "__NO_WSEFTTREDGDD_TIMEOUT__"
        TIMEOUT_EXIT_CODE = 124

        test_cmd = f"python {remote_test_py_path}"
        test_result_file = f"{self.repo_path}/test_result.json"
        command_list = [
            test_cmd,
            f"exit_code=$?",
            f"echo {NO_TIMEOUT_MSG}",
            f"exit $exit_code",
        ]
        command = "; ".join(command_list)
        command = f"timeout {TEST_TIMEOUT+5}s bash -c '{command}'"
        _dlog(f"Running functional test for {self.config.id} ...")
        _dlog(f">>>> Command: `{command}`")

        r = asyncio.run(
            self.deployment.runtime.run_in_session(
                BashAction(command=command, check="silent")
            )
        )
        timeout = r.exit_code == TIMEOUT_EXIT_CODE and NO_TIMEOUT_MSG not in r.output

        if not timeout:
            exit_code = r.exit_code
            output = r.output

            assert NO_TIMEOUT_MSG in r.output
            if exit_code != 0:  # always exit with 0
                _dlog.w(f"Test failed with exit code {exit_code}")
                _dlog.w(f">>>> Output of test.py: `{output}`")
            if exit_code != 0:  # bad script, just crash
                raise RuntimeError(f"Fail to run test.py with exit code {exit_code}")

            # result = {
            #     "compiled": True/False,
            #     "passed": True/False,
            #     "build_log": build_log,
            #     "test_log": test_log,
            # }
            test_result = json.loads(self.read_file(test_result_file))
            test_result["timeout"] = False
            test_result["_test_py_output"] = str(output)
            assert "compiled" in test_result
            assert "passed" in test_result
            assert "build_log" in test_result
            assert "test_log" in test_result

            passed = test_result["passed"]
            compiled = test_result["compiled"]
            if passed:
                assert compiled
            if not compiled:
                assert not passed
            _dlog("Test passed" if passed else "Test failed")
            _dlog(f">>>> Compiled: {compiled}")
            _dlog(f">>>> Passed: {passed}")

            if len(output.splitlines()) > 100:
                output_to_show = "\n\n...\n\n" + ("\n".join(output.splitlines()[-100:]))
            else:
                output_to_show = output
            _dlog(f">>>> output: `{output_to_show}`")

            del output  # the retuned output is not this output
            build_log = test_result["build_log"]
            test_log = test_result["test_log"]
            if not compiled:
                _dlog.w(f">>>> Build log: `{build_log}`")
                output = f"{build_log}\n\nBUILD FAILED!"
            elif not passed:
                _dlog.w(f">>>> Test log: `{test_log}`")
                output = f"{test_log}\n\nTEST FAILED!"
            else:
                output = f"TEST PASSED!"
        else:
            _dlog.w("Timeout while runnning functional test")
            passed = False
            exit_code = -1
            output = f"TEST TIMEOUT WITH {TEST_TIMEOUT} SECONDS!"
            test_result = {
                "timeout": True,
                "passed": False,
                "compiled": False,
                "build_log": None,
                "test_log": None,
                "_test_py_output": None,
            }

        assert passed == test_result["passed"]
        assert timeout == test_result["timeout"]
        assert exit_code in [0, -1]
        if test_result["timeout"]:
            assert not test_result["compiled"]
            assert not test_result["passed"]
            assert not test_result["build_log"]
            assert not test_result["test_log"]
            assert "TEST TIMEOUT WITH" in output
        elif test_result["passed"]:
            assert test_result["compiled"]
            assert not test_result["timeout"]
            assert test_result["build_log"]
            assert test_result["test_log"]
            assert "TEST PASSED!" in output
        elif not test_result["compiled"]:
            assert not test_result["passed"]
            assert not test_result["timeout"]
            assert test_result["build_log"]
            assert "BUILD FAILED!" in output
        elif not test_result["passed"]:
            assert test_result["compiled"]
            assert not test_result["timeout"]
            assert test_result["build_log"]
            assert test_result["test_log"]
            assert "TEST FAILED!" in output
        else:
            assert False, "Not reachable"

        return TestResult(
            passed=passed,
            timeout=timeout,
            exit_code=exit_code,  # 0: test passed/failed, -1: timeout
            raw_output=output,  # the output is not the output of test.py
            extra_info={
                "test_result": test_result,
            },
        )


def make_instance_config(config: Dict[str, Any]):
    def _discard_unused(config: Dict[str, Any], cls: type) -> Dict[str, Any]:
        field_names = {f.name for f in fields(cls)}
        return {k: v for k, v in config.items() if k in field_names}

    return InstanceConfig(**_discard_unused(config, InstanceConfig))


def make_instance(config):
    if isinstance(config, InstanceConfig):
        return Instance(config=config)
    else:
        raise ValueError(f"Unknown instance config type: {type(config)}")
