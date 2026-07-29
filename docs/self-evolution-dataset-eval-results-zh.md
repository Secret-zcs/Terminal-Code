# Self-Evolution Dataset Eval

## Methodology

- This is a deterministic SOP coverage benchmark, not a live model benchmark.
- Public datasets define task families; local JSONL cases are seed cases derived from those families, not copied benchmark instances.
- A case passes when all required terms are covered and no forbidden terms appear in the skill SOP.
- The result measures whether self-evolution made the candidate skill more testable against expected behaviors.

## Dataset Selection Rationale

| Source | Why It Is Included |
|---|---|
| SWE-bench | Repository-level issue repair tests regression reproduction, patch minimality, and verification discipline. |
| AgentBench | Agent/task interaction scenarios test tool-failure recovery and long-horizon safety guardrails. |
| MBPP | Short programming tasks test whether the skill asks for specs, boundary cases, and focused tests. |
| HumanEval | Function-completion tasks test docstring, input/output constraints, assertions, and anti-hardcoding behavior. |

## Before/After Interpretation

- Baseline is the pre-evolution generic coding SOP.
- Evolved is the post-evolution candidate skill SOP distilled from the self-evolution design.
- The observed delta is 100.00% required-term recall across the seed cases.
- A positive delta means the candidate SOP covers more expected guardrails; it does not prove higher live task success.

## Summary

- Dataset: `benchmarks/self_evolution_seed_cases.jsonl`
- Cases: 6
- Baseline Required Recall: 0.00%
- Evolved Required Recall: 100.00%
- Delta Required Recall: 100.00%
- Baseline Passed: 0
- Evolved Passed: 6

## Case Results

| Case | Source | Baseline Recall | Evolved Recall | Evolved Passed |
|---|---|---:|---:|---:|
| swebench_regression_repair | SWE-bench | 0.00% | 100.00% | yes |
| swebench_repo_context_patch | SWE-bench | 0.00% | 100.00% | yes |
| agentbench_tool_failure_recovery | AgentBench | 0.00% | 100.00% | yes |
| agentbench_rewind_safety | AgentBench | 0.00% | 100.00% | yes |
| mbpp_unit_test_synthesis | MBPP | 0.00% | 100.00% | yes |
| humaneval_prompt_to_assertions | HumanEval | 0.00% | 100.00% | yes |

## Source References

| Source | Task Family | Reference |
|---|---|---|
| SWE-bench | real_issue_repair | https://www.swebench.com/SWE-bench/guides/datasets/ |
| SWE-bench | repository_patch | https://www.swebench.com/SWE-bench/guides/datasets/ |
| AgentBench | tool_failure_recovery | https://github.com/THUDM/AgentBench |
| AgentBench | long_horizon_agent_safety | https://github.com/THUDM/AgentBench |
| MBPP | program_synthesis | https://github.com/google-research/google-research/tree/master/mbpp |
| HumanEval | code_generation_eval | https://github.com/openai/human-eval |

## Limitations

- This does not execute a forked agent or run repository tests.
- The baseline and evolved SOPs are fixed strings used for deterministic comparison.
- The next stronger benchmark should replay real tasks in a sandboxed fork-agent runner.
