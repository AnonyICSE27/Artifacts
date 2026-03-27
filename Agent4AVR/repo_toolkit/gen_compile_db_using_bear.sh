if [ $# -lt 2 ]; then
    echo "Usage: $0 <path_to_repo> <build_cli> <build_arg0> <build_arg1> ..."
    exit 1
fi

repo_path=$1
compile_db_path="${repo_path}/compile_commands.json"

if [ -f "$compile_db_path" ]; then
    echo "compile_commands.json already exists in $repo_path"
    exit 0
fi

build_cmd="${@:2}"  # build_cli build_arg0 build_arg1 ...
bear_cmd_try1="rm -f $compile_db_path && bear $build_cmd && exit 0"
bear_cmd_try2="rm -f $compile_db_path && bear -- $build_cmd && exit 0"

cmd="($bear_cmd_try1) || ($bear_cmd_try2) || exit 1"

echo "Cmd: $cmd"
eval $cmd
