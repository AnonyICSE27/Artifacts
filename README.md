## Preparation

1. Clone repository
    ```shell
    git clone https://github.com/AnonyASE26/Artifacts
    ```

2. Prepare conda environment
    * Create: ```conda create --name secb_vr python=3.12.11```
    * Install libraries for this env
        * swe-rex==1.3.0
        * datasets==4.0.0
        * openai==2.6.1
        * tenacity==9.1.2
        * dacite==1.9.2
        * docker==7.1.0
        * libclang==18.1.1
        * langchain==0.3.26
        * langchain-core==0.3.68
        * langchain-openai==0.3.27
        * llm-sandbox==0.3.21
    * See [secb_vr_env.yml](./secb_vr_env.yml) for details

3. Prepare runtime dependencies

    * Refer to [runtime/README.md](./runtime/README.md) to prepare our runtime dependencies.
    * Refer to [clangd_language_server/README.md](./Agent4AVR/repo_toolkit/multilspy/language_servers/clangd_language_server/README.md) to prepare clangd executable.
    * Refer to [gpac/README.md](./SEC-bench+/functional_testing_scripts/gpac/README.md) to prepare artifacts for test suite of gpac.


4. Prepare Configuration Files

    * Configure the LLM and embedding model settings in [llm_config.json](./configs/llm_configs.json) and [embed_config.json](./configs/embed_configs.json)

## Run Issue Resolution on SEC-bench+

### SEC-bench+ Lite (80-instances subset)

```shell
conda activate secb_vr
./launch.sh deepseek-chat /path/to/output_dir --instances :80  # or o3-mini, gpt-4o, ...
```

### SEC-bench+ Full (200-instances fullset)

```shell
conda activate secb_vr
./launch.sh deepseek-chat /path/to/output_dir --instances :200  # or o3-mini, gpt-4o, ...
```

## Run Patch Validation on SEC-bench+

### Transform our output to SWE-Agent format

```shell
conda activate secb_vr
python tools/transform_output_swea_format.py -d /path/to/output_dir -o /path/to/pred_dir --slice :80  # or :200
```

### Run evaluation using SEC-bench+ evaluation script

```shell
conda activate secb_vr
python SEC-bench+/validate_patches.py \
    --resume \
    --verbose \
    --slice :80 or :200 \
    --workers 1 or more \
    --preds /path/to/pred_dir/preds.json
```

The evaluation results will be saved in the directory `/path/to/pred_dir/preds_validation_results.{json,csv}`.

## Appendix

### I. Real-World Issues Resolved by Our Tool

| Project | Vulnerability Type | Issue ID | PR ID | PR Status |
| :--- | :--- | :--- | :--- | :--- |
| liblouis/liblouis | Memory leak | [#1902](https://github.com/liblouis/liblouis/issues/1902) | [#1920](https://github.com/liblouis/liblouis/pull/1920) | **Merged** |
| liblouis/liblouis | Stack buffer overflow | [#1871](https://github.com/liblouis/liblouis/issues/1871) | [#1921](https://github.com/liblouis/liblouis/pull/1921) | **Merged** |
| liblouis/liblouis | Stack buffer overflow | [#1859](https://github.com/liblouis/liblouis/issues/1859) | [#1922](https://github.com/liblouis/liblouis/pull/1922) | **Merged** |
| liblouis/liblouis | Heap buffer overflow | [#1924](https://github.com/liblouis/liblouis/issues/1924) | [#1925](https://github.com/liblouis/liblouis/pull/1925) | **Merged** |
| WebAssembly/wabt | Heap buffer Overflow | [#2557](https://github.com/WebAssembly/wabt/issues/2557) | [#2689](https://github.com/WebAssembly/wabt/pull/2689) | *Under Review* |
| assimp/assimp | Heap buffer Overflow | [#6461](https://github.com/assimp/assimp/issues/6461) | [#6475](https://github.com/assimp/assimp/pull/6475) | `Approved` |
| uclouvain/openjpeg | Heap buffer Overflow | [#1620](https://github.com/uclouvain/openjpeg/issues/1620) | [#1621](https://github.com/uclouvain/openjpeg/pull/1621) | **Merged** |
| stephane/libmodbus | Stack buffer overflow | [#837](https://github.com/stephane/libmodbus/issues/837) | [#839](https://github.com/stephane/libmodbus/pull/839) | `Approved` |


### II. Prompt for CPCAgent

````markdown
You are an expert in software security analysis. Your task is to collect relevant context from the repository that will help your co-developer identify the root cause of a vulnerability and fix it. Carefully review the GitHub issue description, PoC output, and repository structure, then gather only the necessary context from the repository to support the debugging and fixing process.
...
# Available Tools
You have access to the following context collection tools: {tool_names}

# Issue Description
{issue_description}

# Repository Structure (tree format)
{repo_structure}

# Analysis Process
Understand the vulnerability based on the GitHub problem description, focusing on the PoC and sanitizer log (if provided). From the sanitizer log, you can analyze the type of vulnerability, triggering conditions, crash location, and stack trace.
Based on the analysis from step 1, and in combination with the Repository Structure, determine what project context you need to obtain.
Incrementally call tools to obtain the context until you believe you have gathered all the necessary context to assist your co-developer analyze the root cause and prepare for a fix.

# Output Format
Output the context you retrieved using the tools in a specific Markdown format:
Provide the key code lines of each context.
Indicate the source of each context, such as which file it came from, which function, or which location in the file.
Annotate the context with information from the issue description, such as trigger locations in the sanitizer log, stack frame indices, dependencies with crash point (call,called ...), etc. In short, highlight the information from the problem description that relates to the retrieved context.
Explain your understanding of each piece of context, especially why you retrieved it. Be careful not to present uncertain analysis results.
You must never fabricate code or context. If you cannot retrieve a specific function or piece of code, simply omit it rather than making it up. Only include context obtained through the available tools.

Output the results in Markdown format. For example:

### 1. Context 1
```c
( key code lines ... )
```
**Source**: ...
**Annotation**: ...
**Understanding**: ...

### 2. Context 2
...

### Insights
...
````

### III. Prompt for SPAAgent

````markdown
You are … Your task is to analyze a vulnerable C/C++ codebase containing a vulnerability (as described in the GitHub issue description shown below) by generating the safey property assertions. Provide an analysis report (based on the assertion’s execution output) that will help your co-developer identify the root cause of the vulnerability and fix it.

# Available Tools
You have access to the following context collection tools: {tool_names}

# Issue Description
{issue_description_with_context_analysis_report}

# Repository Structure (tree format)
{repo_structure}

# Analysis Process

Safety property is a boolean expression that can be used to check if a vulnerability is violated. In this task, the safety property can be defined as a `SAFETY_PROPERTY_ASSERT(cond,fmt,...)` macro call, where `cond` is the boolean expression to check, and `fmt` is the format string to print if the assertion fails. If the assertion fails, the safety property is violated. And the goal of patching is to make the safety property assertion never fail. So these properties are very important to help your co-developer identify the root cause of the vulnerability and fix it.

1. Run the PoC without making any modifications.
2. Based on the PoC execution results, analyze and locate the crash point, i.e., which operation on which line caused the crash.
3. After locating the crash point, understand the semantics of that operation and generate the first safety property, inserting it before the crash point line (e.g., an accessed index should be less than a certain value, a pointer should not be null, etc.) by using the `apply_edits` tool.
4. Run the PoC to check if the safety property at the crash point is triggered (i.e., whether the assertion fails).
    - If the assertion fails, congratulations! You have successfully found a property violated by this vulnerability. However, you should also verify whether this property is indeed relevant to the vulnerability.
    - If the assertion passes, carefully analyze the PoC output to understand why this vulnerability did not violate the safety property placed before the crash point. Then, generate a more precise assertion. For previously generated property assertions that were not violated, consider whether they represent a true property and whether their output can help your co-developer identify the root cause of the vulnerability and fix it. If they are useful, keep them; there is no need to delete them.
    - If the assertions generated for the crash point always pass, a more fundamental cause should be considered. Try to analyze backwards from the crash point to understand why the assertions generated for the crash point always pass, thereby generating more precise assertions at more appropriate locations.
5. Repeat the series of operations: generating assertions, inserting assertions into the repo via `apply_edits` tool, running the PoC to verify if the property is violated, reflecting on the results, and optimizing assertion generation. Continue until you find properties that you believe sufficiently constrain the vulnerability.

# Output Format
Output the analysis report in **Markdown** format.
1. List the safety property assertions you generated. Separate each one with a blank line. Including:
    - The assertion statement with key contextually relevant code lines.
    - The location of the assertion statement in the codebase.
    - The purpose of the assertion statement, e.g., to check the validity of a variable, to ensure the safety of a pointer, etc.
    - The execution result of the assertion statement, including whether it passed or failed, and the corresponding printed message.
    - Your understanding of the assertion statement and its execution result.
2. Summarize the analysis results, and provide a final conclusion based on them.

Output the results in Markdown format. For example:
### 1. Safety Property Assertion 1
**Assertion**:
```c
( assertion statement ... with key contextually relevant code lines ...)
```
**Location**: ...
**Purpose**: ...
**Message**: ... ( the printed message of the assertion statement ... if multiple executions, summarize the results ... )
**Result**: **PASS** or **FAIL** - ( explain the reason ... )
**Understanding**: ...

### 2. Safety Property Assertion 2
...

### Insights
...
````

### IV. Hyperparameter Selection

<img src="./figures/impact_of_T.png" width="50%">

Most hyperparameters (e.g., $N$, $M$, temperature) are adopted from Agentless. The patch space size ($T=5$) was empirically established via preliminary testing. To further assess the impact of $T$ on performance, we vary $T$ from 1 to 10 and evaluate both the performance and the average cost per issue. The results are shown in Figure above.

As $T$ increases from 1 to 5, performance improves from 49 to 60 issues resolved. However, beyond $T$=5, performance plateaus, with only a slight improvement from $T$=8 to $T$=9, and no further gains at $T$=10. In contrast, the cost steadily increases by 7.0\%, from \$0.0718 at $T$=5 to \$0.0768 at $T$=10. Thus, $T$=5 offers a good balance between performance and cost. Notably, even at $T$=1, our approach fixes 49 issues, achieving a 61.3\% repair rate, outperforming all RQ1 baselines and further demonstrating the effectiveness of our approach.

### V. Statistical Analysis of Tool Usage

<img src="./figures/tool_usage_stats.png" width="50%">

We analyze how our agents employ toolkits by measuring the average tool invocations per issue, including tool frequencies for *CPCAgent* and *SPAAgent*. Results in Figure above show an average of 34 calls per issue, covering code retrieval, analysis, and safety property generation, applying and validation.

As shown in the bar chart, *CPCAgent* uses `search_code_element`, `read_code`, and `resolve_code_symbol` for *static* analysis, while *SPAAgent* frequently invokes `run_poc`, `apply_edits`, and `run_python_code` for *dynamic* property analysis. On average, *CPCAgent* performs 5.7 searches, 2.9 reads, and 2.4 symbol resolutions per issue; *SPAAgent* executes PoC 6.2 times, applies 6.3 edits, and runs Python 0.96 times.

**Code Symbol Resolution.** The upper-right pie chart shows that *CPCAgent* performs 62.9% definition (*CPCAgent*-Def) and 5.7% reference (*CPCAgent*-Ref) resolutions, while *SPAAgent* allocates 26.8% and 4.6% to the same categories, respectively.

**Python Code Execution.** The lower-right pie chart summarizes `run_python_code` invocations, each manually labeled by functionality. Among them, 34.5% retrieve *poc* outputs (e.g., calling `get_poc_output()`), 32.8% perform *string* operations (e.g., parsing string with regex). Interestingly, 12.0% exhibit a *think* tag, where generated Python code prints hardcoded text without computation. In fact, these calls, though non-functional, externalize the agent's intermediate reasoning, similar to the "think" mechanisms in OpenHands, Anthropic's recent practice, and ByteDance's TRAE Agent that facilitate complex multi-tool coordination. Another 5.9% involve integer arithmetic (e.g., range comparisons, arithmetic, or bitwise operations), and seven attempts included forbidden actions such as file system access (*fs*), file reading (*read*), or command execution (*cmd*), all correctly blocked by the sandboxed execution environment.

Overall, VulnResolver exhibits phase-dependent toolkit usage: *CPCAgent* focuses on static context collection, while *SPAAgent* emphasizes property analysis, generation, and dynamic validation.
