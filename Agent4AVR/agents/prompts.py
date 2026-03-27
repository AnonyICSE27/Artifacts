DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."  # Refer to https://github.com/OpenAutoCoder/Agentless/blob/main/agentless/util/api_requests.py#L28


PRE_COLLECT_CONTEXT_PROMPT = """\
You are an expert in software security analysis.
Your task is to collect relevant context from the repository that will help your co-developer identify the root cause of a vulnerability and fix it.
Carefully review the GitHub problem description, PoC output, and repository structure, then gather only the necessary context from the repository to support the debugging and fixing process.
You MUST use the available tools to collect the context. You must never fabricate code or context. If you cannot retrieve a specific function or piece of code, simply omit it rather than making it up. Only include context obtained through the available tools.

# Available Tools

You have access to the following context collection tools: {tool_names}

# GitHub Problem Description
{issue_description}

# PoC Output

The PoC triggered a crash, and the sanitizer log shows the following:

{poc_output}

**NOTE:** The PoC output above is obtained by actually running the PoC, and it may differ from the sanitizer log shown in the GitHub issue description. When invoking tools (especially when specifying line numbers), always rely on the **PoC output** rather than the sanitizer log from the GitHub description.

# Repository Structure (tree format)
{repo_structure}

# Analysis Process
1. Understand the vulnerability based on the GitHub problem description, focusing on the PoC and sanitizer log (if provided). From the sanitizer log, you can analyze the type of vulnerability, triggering conditions, crash location, and stack trace.
2. Based on the analysis from step 1, and in combination with the Repository Structure, determine what project context you need to obtain — for example, the code of a specific function in a file, or code at a specific location in a file.
    **NOTE**: The code paths in the GitHub Problem Description are generated when the user reproduced the issue locally. Only the file paths in the Repository Structure are fully correct. You should analyze the code mentioned in the Problem Description, then confirm the accurate path based on the Repository Structure.
3. Incrementally call tools to obtain the context until you believe you have gathered all the necessary context, based on the Problem Description, to assist your co-developer analyze the root cause and prepare for a fix. Do not attempt to obtain the entire project code — only the context you consider necessary to understand the vulnerability root cause and its resolution.
    (1) First, collect the necessary context mentioned in the Problem Description.
    (2) Next, collect the other context that is relevant to the vulnerability.

# Output Format
Output the context you retrieved using the tools in a specific Markdown format:
1. Provide the key code lines of each context.
2. Indicate the source of each context, such as which file it came from, which function, or which location in the file.
3. Annotate the context with information from the problem description, such as trigger locations in the sanitizer log, stack frame indices, dependencies with crash point (call,called ...), etc. In short, highlight the information from the problem description that relates to the retrieved context.
4. Explain your understanding of each piece of context, especially why you retrieved it. Be careful not to present uncertain analysis results.
5. You must never fabricate code or context. If you cannot retrieve a specific function or piece of code, simply omit it rather than making it up. Only include context obtained through the available tools.
6. Separate each context block with a blank line.

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

...

### Insights & Conclusion
...

# Guidelines
* Your only objective is: based on the Problem Description and Repository Structure, retrieve the project context that you consider necessary. You do not need to attempt analyzing the root cause or fix directly — only gather the context that will allow such analysis later.
* All context you provide must come from tool calls; do not fabricate any context yourself.
* You have a limited number of tool calls ({max_iterations} times). If you exceed the limit, you will be forbidden from using the tools. Therefore, first retrieve the most important context, then the next most important, and so on.
* If you believe you have retrieved all the necessary context, stop calling tools. Organize the context you have, and then output the result in Markdown format.
"""


PRE_COLLECT_CONTEXT_PROMPT_FOR_OPENAI_MODELS = """\
You are an expert in software security analysis.
Your task is to collect relevant context from the repository that will help your co-developer identify the root cause of a vulnerability and fix it.
Carefully review the GitHub problem description, PoC output, and repository structure, then gather only the necessary context from the repository to support the debugging and fixing process.
You MUST use the available tools to collect the context. You must never fabricate code or context. If you cannot retrieve a specific function or piece of code, simply omit it rather than making it up. Only include context obtained through the available tools.

# Available Tools

You have access to the following context collection tools: {tool_names}

# GitHub Problem Description
{issue_description}

# PoC Output

The PoC triggered a crash, and the sanitizer log shows the following:

{poc_output}

**NOTE:** The PoC output above is obtained by actually running the PoC, and it may differ from the sanitizer log shown in the GitHub issue description. When invoking tools (especially when specifying line numbers), always rely on the **PoC output** rather than the sanitizer log from the GitHub description.

# Repository Structure (tree format)
{repo_structure}

# Analysis Process
1. Understand the vulnerability based on the GitHub problem description, focusing on the PoC and sanitizer log (if provided). From the sanitizer log, you can analyze the type of vulnerability, triggering conditions, crash location, and stack trace.
2. Based on the analysis from step 1, and in combination with the Repository Structure, determine what project context you need to obtain — for example, the code of a specific function in a file, or code at a specific location in a file.
    **NOTE**: The code paths in the GitHub Problem Description are generated when the user reproduced the issue locally. Only the file paths in the Repository Structure are fully correct. You should analyze the code mentioned in the Problem Description, then confirm the accurate path based on the Repository Structure.
3. Incrementally call tools to obtain the context until you believe you have gathered all the necessary context, based on the Problem Description, to assist your co-developer analyze the root cause and prepare for a fix. Do not attempt to obtain the entire project code — only the context you consider necessary to understand the vulnerability root cause and its resolution.
    (1) First, collect the necessary context mentioned in the Problem Description.
    (2) Next, collect the other context that is relevant to the vulnerability.

# Output Format

After gathering all the necessary context, generate a context analysis report. Specifically:
Output the context you retrieved using the tools in a specific Markdown format:
1. Provide the key code lines of each context.
2. Indicate the source of each context, such as which file it came from, which function, or which location in the file.
3. Annotate the context with information from the problem description, such as trigger locations in the sanitizer log, stack frame indices, dependencies with crash point (call,called ...), etc. In short, highlight the information from the problem description that relates to the retrieved context.
4. Explain your understanding of each piece of context, especially why you retrieved it. Be careful not to present uncertain analysis results.
5. You must never fabricate code or context. If you cannot retrieve a specific function or piece of code, simply omit it rather than making it up. Only include context obtained through the available tools.
6. Separate each context block with a blank line.

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

...

### Insights & Conclusion
...

# Guidelines
* Your only objective is: based on the Problem Description and Repository Structure, retrieve the project context that you consider necessary. You do not need to attempt analyzing the root cause or fix directly — only gather the context that will allow such analysis later.
* All context you provide must come from tool calls; do not fabricate any context yourself.
* You have a limited number of tool calls ({max_iterations} times). If you exceed the limit, you will be forbidden from using the tools. Therefore, first retrieve the most important context, then the next most important, and so on.
* If you believe you have retrieved all the necessary context, stop calling tools. Organize the context you have, and then output the result in Markdown format.
"""


SAFETY_PROPERTY_ANALYSIS_PROMPT = """\
You are a highly skilled expert in software security analysis.

Your task is to analyze a vulnerable C/C++ codebase containing a vulnerability (as described in the GitHub problem description shown below) by generating the safey property assertions. And:
    - (1) Provide an analysis report (based on the assertion's execution output) that will help your co-developer identify the root cause of the vulnerability and fix it.
    - (2) Your generated safety property assertions will be saved and used to feedback the vulnerability patching process.

**VERY IMPORTANT**:
    - YOU MUST ONLY ANALYZE **THIS VULNERABILITY** DESCRIBED IN THE GITHUB PROBLEM DESCRIPTION.
    - OTHER POTENTIAL VULNERABILITIES IN THE CODEBASE ARE NOT YOURS.
    - THUS, YOU SHOULD ONLY FOCUS ON THE **RELEVANT** SAFETY PROPERTY ASSERTIONS FOR THIS VULNERABILITY.

Important Notes:
* You MUST use the available tools to perform the analysis, safety property assertion generation, validation, and property refinement.
* Your:
    (1) analysis report will be provided to your co-developer to help them identify the root cause of the vulnerability and fix it.
    (2) generated safety property assertions will be automatically saved and used to feedback such vulnerability patching process.
* NEVER fabricate results. Only use outputs from the tools.
* Total tool calls allowed: {max_iterations}.
* Stop once you have generated the sufficient safety property assertions for this vulnerability.

---

# Available Tools

You have access to the these tools: {tool_names}
{tool_use_guidance}

# GitHub Problem Description

{issue_description}

# Repository Structure (tree format)

{repo_structure}

# Working Process

Safety Property is a boolean expression that can be used to check if a vulnerability is violated.
In this task, the safety property can be defined as a `SAFETY_PROPERTY_ASSERT(cond,fmt,...)` macro call, where `cond` is the boolean expression to check, and `fmt` is the format string to print if the assertion fails.
If the assertion fails, the safety property is violated. And the goal of patching is to make the safety property assertion never fail.
So These properties are very important to help your co-developer identify the root cause of the vulnerability and fix it.

The following steps are the working process:

1.  Run the PoC without making any modifications.
2.  Based on the PoC execution results, analyze and locate the crash point, i.e., which operation on which line caused the crash.
    -   In this step, if the expression on the crash point line is complex, with multiple operations (e.g., multiple pointer dereferences), making it difficult to pinpoint the exact crash point based on the line number, you can use editing tools to split this line into several equivalent lines. Then run the PoC again to analyze the specific crash point.
3.  After locating the crash point, understand the semantics of that operation and generate the first safety property, inserting it *before* the crash point line (e.g., an accessed index should be less than a certain value, a pointer should not be null, etc.) by using the `apply_edits` tool.
4.  Run the PoC to check if the safety property at the crash point is triggered (i.e., whether the assertion fails).
    -   If the assertion fails, congratulations! You have successfully found a property violated by this vulnerability. However, you should also verify whether this property is indeed relevant to the vulnerability (for example, `assert(false)` will always fail, but it is clearly not a meaningful safety property).
    -   If the assertion passes, carefully analyze the PoC output to understand why this vulnerability did not violate the safety property placed before the crash point. Then, generate a more precise assertion.
        -   For previously generated property assertions that were not violated, consider whether they represent a true property and whether their output can help your co-developer identify the root cause of the vulnerability and fix it. If they are useful, keep them; there is no need to delete them.
5.  If the assertions generated for the crash point always pass, a more fundamental cause should be considered. Try to analyze backwards from the crash point to understand why the assertions generated for the crash point always pass, thereby generating more precise assertions at more appropriate locations.
6.  Repeat the series of operations: generating assertions, inserting assertions into the repo via `apply_edits` tool, running the PoC to verify if the property is violated, reflecting on the results, and optimizing assertion generation. Continue until you find properties that you believe sufficiently constrain the vulnerability.
7.  Finally, run the PoC once more, analyze the output to form an analysis report. List the assertions you attempted to generate and their execution results. Conclude by summarizing the insights you gained about this vulnerability from the entire process of generating and running assertions, to aid the subsequent vulnerability root cause analysis and fixing process.

Note, the assertions supported by this project are defined as follows. Please do not use other methods.
```c
#define SAFETY_PROPERTY_ASSERT(cond, fmt, ...) do {{ \\
    if (!(cond)) {{ \\
        printf("[FAIL] %s:%d | %s | " fmt "\n", __FILE__, __LINE__, #cond, ##__VA_ARGS__); \\
        fflush(stdout); \\
    }} else {{ \\
        printf("[PASS] %s:%d | %s | " fmt "\n", __FILE__, __LINE__, #cond, ##__VA_ARGS__); \\
        fflush(stdout); \\
    }} \\
}} while(0)
```
For example: `SAFETY_PROPERTY_ASSERT(index < len + 1, "Value check: index=%d, len=%zu, len=%zx", index, len, len);`
Provide the condition via `cond`, and always provide auxiliary information via `fmt` and variadic arguments to help you understand the specific variable values and the execution status of this assertion.
Your goal is to generate a sufficient number of violated safety property assertions to help your co-developer identify the root cause of the vulnerability and fix it.
Also, for those safety property assertions that were not violated, please keep them if they are useful, as they may also contain valuable information.

Additionally, it is strongly recommended that you insert `SAFETY_PROPERTY_ASSERT` directly *before* the existing line of code, meaning the assertion statement does not occupy a line by itself. This preserves the original line numbers, which is beneficial for leveraging your previous analysis results.
For example, we recommend:
```c
SAFETY_PROPERTY_ASSERT(index < len + 1, "Value check: index=%d, len=%zu, len=%zx", index, len, len); K = buffer[index];
```
Instead of:
```c
SAFETY_PROPERTY_ASSERT(index < len + 1, "Value check: index=%d, len=%zu, len=%zx", index, len, len);
K = buffer[index];
```

# Output Format

Output the analysis report in **Markdown** format.

1. List the safety property assertions you generated. Separate each one with a blank line. Including:
    - The assertion statement with key contextually relevant code lines.
    - The location of the assertion statement in the codebase.
    - The purpose of the assertion statement, e.g., to check the validity of a variable, to ensure the safety of a pointer, etc.
    - The execution result of the assertion statement, including whether it passed or failed, and the corresponding printed message.
    - Your understanding of the assertion statement and its execution result.
2. Summarize the analysis results, and provide a final conclusion based on them. Be careful not to present irrelevant results.

## Example

```markdown
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

### Insights & Conclusion
...
```

# Guidelines
* Your only objective is: to analyze the vulnerable C/C++ codebase (as described in the GitHub problem description) with generating & validating safety properties and provide an analysis report that will help your co-developer identify the root cause of the vulnerability and fix it. You do **not** need to directly analyze the root cause or propose fixes — only provide the analysis report and safety property assertions that enables such work later.
* You have a limited number of tool calls ({max_iterations} times). If you exceed this limit, you will be forbidden from using the tools.
* Once you have generated the sufficient safety property assertions for this vulnerability, stop calling tools, then organize the results and produce the analysis report.
* Your applied edits will be collected after you generate the final analysis report, so do NOT rollback them at the end.
    - The `rollback` tool is available for use during your iterative analysis and debugging process if you need to revert a series of changes. 
    - Your final goal is to leave behind the set of `SAFETY_PROPERTY_ASSERT` statements that you have validated as useful for identifying the vulnerability's root cause.
    - You do not need to cleanup these final edits; your co-developer will directly re-use this final version of the repository containing your assertions.
* You MUST output the analysis report in Markdown format, but do not wrap it with "```markdown" and "```".
"""


SAFETY_PROPERTY_ANALYSIS_PROMPT_FOR_OPENAI_MODELS = """\
You are a highly skilled expert in software security analysis.

Your task is to analyze a vulnerable C/C++ codebase containing a vulnerability (as described in the GitHub problem description shown below) by generating the safey property assertions. And:
    - (1) Provide an analysis report (based on the assertion's execution output) that will help your co-developer identify the root cause of the vulnerability and fix it.
    - (2) Your generated safety property assertions will be saved and used to feedback the vulnerability patching process.

**VERY IMPORTANT**:
    - YOU MUST ONLY ANALYZE **THIS VULNERABILITY** DESCRIBED IN THE GITHUB PROBLEM DESCRIPTION.
    - OTHER POTENTIAL VULNERABILITIES IN THE CODEBASE ARE NOT YOURS.
    - THUS, YOU SHOULD ONLY FOCUS ON THE **RELEVANT** SAFETY PROPERTY ASSERTIONS FOR THIS VULNERABILITY.

Important Notes:
* You MUST use the available tools to perform the analysis, safety property assertion generation, validation, and property refinement.
* Your:
    (1) analysis report will be provided to your co-developer to help them identify the root cause of the vulnerability and fix it.
    (2) generated safety property assertions will be automatically saved and used to feedback such vulnerability patching process.
* NEVER fabricate results. Only use outputs from the tools.
* Stop once you have generated the sufficient safety property assertions for this vulnerability.

---

# Available Tools

You have access to the these tools: {tool_names}
{tool_use_guidance}

# GitHub Problem Description

{issue_description}

# Repository Structure (tree format)

{repo_structure}

# Working Process

Safety Property is a boolean expression that can be used to check if a vulnerability is violated.
In this task, the safety property can be defined as a `SAFETY_PROPERTY_ASSERT(cond,fmt,...)` macro call, where `cond` is the boolean expression to check, and `fmt` is the format string to print if the assertion fails.
If the assertion fails, the safety property is violated. And the goal of patching is to make the safety property assertion never fail.
So These properties are very important to help your co-developer identify the root cause of the vulnerability and fix it.

The following steps are the working process:

1.  Run the PoC without making any modifications.
2.  Based on the PoC execution results, analyze and locate the crash point, i.e., which operation on which line caused the crash.
    -   In this step, if the expression on the crash point line is complex, with multiple operations (e.g., multiple pointer dereferences), making it difficult to pinpoint the exact crash point based on the line number, you can use editing tools to split this line into several equivalent lines. Then run the PoC again to analyze the specific crash point.
3.  After locating the crash point, understand the semantics of that operation and generate the first safety property, inserting it *before* the crash point line (e.g., an accessed index should be less than a certain value, a pointer should not be null, etc.) by using the `apply_edits` tool.
4.  Run the PoC to check if the safety property at the crash point is triggered (i.e., whether the assertion fails).
    -   If the assertion fails, congratulations! You have successfully found a property violated by this vulnerability. However, you should also verify whether this property is indeed relevant to the vulnerability (for example, `assert(false)` will always fail, but it is clearly not a meaningful safety property).
    -   If the assertion passes, carefully analyze the PoC output to understand why this vulnerability did not violate the safety property placed before the crash point. Then, generate a more precise assertion.
        -   For previously generated property assertions that were not violated, consider whether they represent a true property and whether their output can help your co-developer identify the root cause of the vulnerability and fix it. If they are useful, keep them; there is no need to delete them.
5.  If the assertions generated for the crash point always pass, a more fundamental cause should be considered. Try to analyze backwards from the crash point to understand why the assertions generated for the crash point always pass, thereby generating more precise assertions at more appropriate locations.
6.  Repeat the series of operations: generating assertions, inserting assertions into the repo via `apply_edits` tool, running the PoC to verify if the property is violated, reflecting on the results, and optimizing assertion generation. Continue until you find properties that you believe sufficiently constrain the vulnerability.
7.  Finally, **run the PoC once more**, analyze the output to form an analysis report. List the assertions you attempted to generate and their execution results. Conclude by summarizing the insights you gained about this vulnerability from the entire process of generating and running assertions, to aid the subsequent vulnerability root cause analysis and fixing process.

You should try step 3 and 4 **multiple** times until you find property assertions that are violated by the vulnerability, i.e., the assertion fails when you insert them, re-run the PoC and check the output.
You should try step 3 and 4 **multiple** times until you find property assertions that are violated by the vulnerability, i.e., the assertion fails when you insert them, re-run the PoC and check the output.
You should try step 3 and 4 **multiple** times until you find property assertions that are violated by the vulnerability, i.e., the assertion fails when you insert them, re-run the PoC and check the output.
You should try step 3 and 4 **multiple** times until you find property assertions that are violated by the vulnerability, i.e., the assertion fails when you insert them, re-run the PoC and check the output.
You should try step 3 and 4 **multiple** times until you find property assertions that are violated by the vulnerability, i.e., the assertion fails when you insert them, re-run the PoC and check the output.
You should try step 3 and 4 **multiple** times until you find property assertions that are violated by the vulnerability, i.e., the assertion fails when you insert them, re-run the PoC and check the output.

You should generate one and validate one, and then generate the next one. Do not incrementally generate multiple assertions at once.
If the PoC output is too long and cannot find the assertion that you are looking for, you should analyze the output by using `run_python_code`. Do NOT directly skip the analysis.

You should generate one and validate one, and then generate the next one. Do not incrementally generate multiple assertions at once.
If the PoC output is too long and cannot find the assertion that you are looking for, you should analyze the output by using `run_python_code`. Do NOT directly skip the analysis.

You should generate one and validate one, and then generate the next one. Do not incrementally generate multiple assertions at once.
If the PoC output is too long and cannot find the assertion that you are looking for, you should analyze the output by using `run_python_code`. Do NOT directly skip the analysis.

You should try your best to generate ALL (i.e., may be multiple) possible violated safety property assertions until you receive a "Error: Tool call limit reached ..." message.
You should try your best to generate ALL (i.e., may be multiple) possible violated safety property assertions until you receive a "Error: Tool call limit reached ..." message.
You should try your best to generate ALL (i.e., may be multiple) possible violated safety property assertions until you receive a "Error: Tool call limit reached ..." message.
You should try your best to generate ALL (i.e., may be multiple) possible violated safety property assertions until you receive a "Error: Tool call limit reached ..." message.
You should try your best to generate ALL (i.e., may be multiple) possible violated safety property assertions until you receive a "Error: Tool call limit reached ..." message.

Note, the assertions supported by this project are defined as follows. Please do not use other methods.
```c
#define SAFETY_PROPERTY_ASSERT(cond, fmt, ...) do {{ \\
    if (!(cond)) {{ \\
        printf("[FAIL] %s:%d | %s | " fmt "\\n", __FILE__, __LINE__, #cond, ##__VA_ARGS__); \\
        fflush(stdout); \\
    }} else {{ \\
        printf("[PASS] %s:%d | %s | " fmt "\\n", __FILE__, __LINE__, #cond, ##__VA_ARGS__); \\
        fflush(stdout); \\
    }} \\
}} while(0)
```
For example: `SAFETY_PROPERTY_ASSERT(index < len + 1, "Value check: index=%d, len=%zu, len=%zx", index, len, len);`
Provide the condition via `cond`, and always provide auxiliary information via `fmt` and variadic arguments to help you understand the specific variable values and the execution status of this assertion.
Your goal is to generate a sufficient number of violated safety property assertions to help your co-developer identify the root cause of the vulnerability and fix it.
Also, for those safety property assertions that were not violated, please keep them if they are useful, as they may also contain valuable information.

Additionally, it is strongly recommended that you insert `SAFETY_PROPERTY_ASSERT` directly *before* the existing line of code, meaning the assertion statement does not occupy a line by itself. This preserves the original line numbers, which is beneficial for leveraging your previous analysis results.
For example, we recommend:
```c
SAFETY_PROPERTY_ASSERT(index < len + 1, "Value check: index=%d, len=%zu, len=%zx", index, len, len); K = buffer[index];
```
Instead of:
```c
SAFETY_PROPERTY_ASSERT(index < len + 1, "Value check: index=%d, len=%zu, len=%zx", index, len, len);
K = buffer[index];
```

If the assertion statement is passed, it will print a message starting with "[PASS]".
If the assertion statement is violated, it will print a message starting with "[FAIL]".
If no printed message is shown, it means the assertion statement is not executed.

If the assertion statement is passed, it will print a message starting with "[PASS]".
If the assertion statement is violated, it will print a message starting with "[FAIL]".
If no printed message is shown, it means the assertion statement is not executed.

If the assertion statement is passed, it will print a message starting with "[PASS]".
If the assertion statement is violated, it will print a message starting with "[FAIL]".
If no printed message is shown, it means the assertion statement is not executed.

# Output Format

After generating all the necessary assertions, inserting them and running the PoC to check them, generate a safety property analysis report. Specifically:
Output the analysis report in **Markdown** format.
1. List the safety property assertions you generated. Separate each one with a blank line. Including:
    - The assertion statement with key contextually relevant code lines.
    - The location of the assertion statement in the codebase.
    - The purpose of the assertion statement, e.g., to check the validity of a variable, to ensure the safety of a pointer, etc.
    - The execution result of the assertion statement, including whether it passed or failed, and the corresponding printed message.
    - Your understanding of the assertion statement and its execution result.
2. Summarize the analysis results, and provide a final conclusion based on them. Be careful not to present irrelevant results.

## Example

```markdown
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

### Insights & Conclusion
...
```

# Guidelines
* Your only objective is: to analyze the vulnerable C/C++ codebase (as described in the GitHub problem description) with generating & validating safety properties and provide an analysis report that will help your co-developer identify the root cause of the vulnerability and fix it. You do **not** need to directly analyze the root cause or propose fixes — only provide the analysis report and safety property assertions that enables such work later.
* You have a limited number of tool calls. If you exceed this limit, you will be forbidden from using the tools. Do not worry, the tool will output a error message to help detect this situation.
* Before generating all necessary safety property assertions violated by the vulnerability, do not give up. Try, and keep generating until you receive a "Error: Tool call limit reached ..." message.
* Your applied edits will be collected after you generate the final analysis report, so do NOT rollback them at the end.
    - The `rollback` tool is available for use during your iterative analysis and debugging process if you need to revert a series of changes. 
    - Your final goal is to leave behind the set of `SAFETY_PROPERTY_ASSERT` statements that you have validated as useful for identifying the vulnerability's root cause.
    - You do not need to cleanup these final edits; your co-developer will directly re-use this final version of the repository containing your assertions.
* You MUST output the analysis report in Markdown format, but do not wrap it with "```markdown" and "```".
"""


OBTAIN_SUSPICIOUS_FILES_PROMPT = """\
Please look through the following GitHub problem description and Repository structure and provide a list of files that one would need to edit to fix the problem.

# GitHub Problem Description
{issue_description}


# Repository Structure (tree format)
{repo_structure}

Please only provide the full path and return at most {max_files} files.
The returned files should be separated by new lines ordered by most to least important and wrapped with ```
For example:
```
{repo_name}/folder1/file1.{{c,h,cpp,cc,cxx,C,hpp,hh,hxx,H}}
{repo_name}/folder2/folder3/file2.{{c,h,cpp,cc,cxx,C,hpp,hh,hxx,H}}
```
Note that all file paths should be start with {repo_name}/
Wrap all suspicious file paths in ONE SINGLE markdown code block, and each file path should be on a new line.
"""


OBTAIN_IRRELEVANT_FILES_PROMPT = """\
Please look through the following GitHub problem description and Repository structure and provide a list of folders that are irrelevant to fixing the problem.
Note that irrelevant folders are those that do not need to be modified and are safe to ignored when trying to solve this problem.

# GitHub Problem Description
{issue_description}


# Repository Structure
{repo_structure}

Please only provide the full path.
Remember that any subfolders will be considered as irrelevant if you provide the parent folder.
Please ensure that the provided irrelevant folders do not include any important files needed to fix the problem
The returned folders should be separated by new lines and wrapped with ```
For example:
```
{repo_name}/folder1/
{repo_name}/folder2/folder3/
{repo_name}/folder4/folder5/
```

Note that all paths should be start with {repo_name}/
"""


# NOTE: Use response_format={"type": "json_object"} get JSON response
OBTAIN_SUSPICIOUS_ELEMENTS_PROMPT = """\
Please look through the following GitHub Problem Description and the Skeleton of Relevant Files.
Identify all locations that need inspection or editing to fix the problem, including directly related areas as well as any potentially related code elements, including "class", "struct", "union", "enum", "function", "macro", and "global_variable".
Note that member/static functions in "class" and "struct" are also "function"s.
For each location you provide, give the element type and the element path in JSON format.

# GitHub Problem Description
{issue_description}


# Skeleton of Relevant Files
{file_skeletons}


Please provide the complete list of identified elements.
Note that if you include a class/struct, you do not need to list its specific functions.
You can include either the entire class/struct or don't include the class and instead include specific methods in the class/struct.
Write your response in JSON format.
### Example Output:
{{
    "elements": [
        {{
            "element_type": "function",
            "element_path": "path1/file1.c::func2"
        }},
        {{
            "element_type": "struct",
            "element_path": "path1/file1.c::Struct1"
        }},
        {{
            "element_type": "macro",
            "element_path": "path1/path2/file2.c::MACRO1"
        }},
        {{
            "element_type": "global_variable",
            "element_path": "path1/path2/file3.cpp::GLOBAL_VAR1"
        }},
        {{
            "element_type": "class",
            "element_path": "path2/path3/file4.cxx::Class1"
        }},
        {{
            "element_type": "function",
            "element_path": "file5.h::Class2::func1"
        }}
    ]
}}

Note: 
1. returned json should be a dictionary with one key: "elements".
2. "elements" should be a list of dictionaries. Each dictionary should have two keys: "element_type" and "element_path".

Note: union, enum are similar to struct and class."""


SIMPLE_PATCH_GENERATION_PROMPT = """
We are currently solving the following issue within our repository. Here is the issue text:
### BEGIN ISSUE ###
{issue_description}
### END ISSUE ###

Below are some code segments, each from a relevant file. One or more of these files may contain bugs.
Note we only provide the key bug code snippets from the bug file, for the rest of the code sections we use ... to omit.
### BEGIN SUSPICIOUS FILES ###
{suspicious_files}
### END SUSPICIOUS FILES ###

Please first localize the bug based on the issue description, and then generate *SEARCH/REPLACE* edits to fix the issue.

Every *SEARCH/REPLACE* edit must use this format:
1. The file path
2. The start of search block: <<<<<<< SEARCH
3. A contiguous chunk of lines to search for in the existing source code
4. The dividing line: =======
5. The lines to replace into the source code
6. The end of the replace block: >>>>>>> REPLACE

Here is an example:

```diff
### path/to/example.c
<<<<<<< SEARCH
int b = *ptr * 2;
=======
int b = 0;
if (ptr != NULL) {{
    b = *ptr * 2;
}}
>>>>>>> REPLACE
```

Please note that the *SEARCH/REPLACE* edit REQUIRES PROPER INDENTATION. If you would like to add the line '        printf(x)', you must fully write that out, with all those spaces before the code!
Please note that you must provide sufficient *SEARCH* edit context (No less than 3 lines of code) to ensure that the code location can be successfully searched!
Please note that you can't use "..." or any other to alter and ignore the original code content, you must keep the original code format and content in the *SEARCH/REPLACE* edit!
Please note that your MUST provide the file path at the first line of each *SEARCH/REPLACE* edit with the format: '### path/to/a/file.c'
Wrap the generated *SEARCH/REPLACE* edits in code blocks ```diff...```
Note that if multiple *SEARCH/REPLACE* edits are needed, please wrap each edit in a separate code block, thereby ensuring responsing multiple blocks.
"""
