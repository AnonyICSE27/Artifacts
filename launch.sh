#! /bin/bash

if [ $# -lt 1 ]; then
    echo "Usage: $0 <model_name> <output_dir> [additional_args]"
    exit 1
fi

model_name=$1
output_dir=$2
additional_args=${@:3}

model_configs_file=./configs/llm_configs.json
embed_model_name=text-embedding-3-small
embed_model_configs_file=./configs/embed_configs.json

simple_vra__max_loc_files_with_prompting=3
simple_vra__max_loc_files_with_retrieving=3
simple_vra__chunk_size_when_retrieving=512
simple_vra__chunk_overlap_when_retrieving=0
simple_vra__context_window=10
simple_vra__num_pacthes_to_gen=5 # patch size

dataset="SEC-bench/SEC-bench"
dataset_split="eval"
timestamp=$(date +"%Y_%m_%d_%H%M%S")
log_file="${output_dir}/__log_d_${timestamp}.ansi"

echo "+================== VulnResolver =================="
echo "| LLM Name: ${model_name}"
echo "| Patch Space: ${simple_vra__num_pacthes_to_gen}"
echo "| Benchmark: ${dataset} (${dataset_split})"
echo "| Log file: ${log_file}"
echo "| Ouput dir: ${output_dir}"
echo "+================================================"

export STATIC_ANALYSIS_TOOLS_DIR="./runtime" # "STATIC_ANALYSIS_TOOLS_DIR", just a name, not meaningfull here
export PRE_COLLECT_CONTEXT_MAX_TOOL_CALLS=10
export SAFETY_PROPERTY_ANALYSIS_MAX_TOOL_CALLS=40
export MAX_RUN_POC_OUTPUT_CHARS=8000
export MAX_PYTHON_INTERPRETER_OUTPUT_CHARS=8000
export MAX_PYTHON_INTERPRETER_TIMEOUT_SECONDS=600 # 10min
export MAX_VAL_OUTPUT_CHARS=8000
export ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT=1
export ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_FOR_PRECC=1
export ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_V2=1
export ENABLE_CODE_SYMBOL_ANALYSIS_TOOLKIT_V3=1
export INIT_CHECK_COMPILABLE=1

# export GEN_ALL_THEN_VAL=1
export LOAD_PATCH_IF_EXIST=1
cache_dir="$HOME/.AgentAPR4Vul/cache/global_cache"

mkdir -p ${cache_dir}
mkdir -p ${output_dir}
python -u -m Agent4AVR \
    --experiment "simple_vra" \
    --model_name ${model_name} \
    --model_configs_file ${model_configs_file} \
    --embed_model_name ${embed_model_name} \
    --embed_model_configs_file ${embed_model_configs_file} \
    --dataset ${dataset} \
    --dataset_split ${dataset_split} \
    --output_dir ${output_dir} \
    --cache_dir ${cache_dir} \
    --skip_if_error \
    --simple_vra__max_loc_files_with_prompting ${simple_vra__max_loc_files_with_prompting} \
    --simple_vra__max_loc_files_with_retrieving ${simple_vra__max_loc_files_with_retrieving} \
    --simple_vra__chunk_size_when_retrieving ${simple_vra__chunk_size_when_retrieving} \
    --simple_vra__chunk_overlap_when_retrieving ${simple_vra__chunk_overlap_when_retrieving} \
    --simple_vra__context_window ${simple_vra__context_window} \
    --simple_vra__num_pacthes_to_gen ${simple_vra__num_pacthes_to_gen} \
    --simple_vra__enable_context_pre_collection \
    --simple_vra__enable_safety_property_analysis \
    ${additional_args} \
    2>&1 | tee ${log_file}
