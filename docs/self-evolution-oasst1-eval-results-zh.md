# Self-Evolution Dataset Eval

## Methodology

- This is a deterministic SOP coverage benchmark, not a live model benchmark.
- Local JSONL cases are either public-benchmark seed cases or privacy-preserving derived task signals; raw conversations are not included.
- A case passes when all required terms are covered and no forbidden terms appear in the skill SOP.
- The result measures whether self-evolution made the candidate skill more testable against expected behaviors.

## Dataset Selection Rationale

| Source | Why It Is Included |
|---|---|
| OASST1 | Human-generated multi-turn conversations provide real follow-up and correction patterns; only sanitized task signals are retained. |

## Before/After Interpretation

- Baseline is the pre-evolution generic coding SOP.
- Evolved is the post-evolution candidate skill SOP distilled from the self-evolution design.
- The observed delta is 100.00% required-term recall across the evaluated cases.
- A positive delta means the candidate SOP covers more expected guardrails; it does not prove higher live task success.

## Summary

- Dataset: `benchmarks/oasst1_derived_cases.jsonl`
- Cases: 19
- Baseline Required Recall: 0.00%
- Evolved Required Recall: 100.00%
- Delta Required Recall: 100.00%
- Baseline Passed: 0
- Evolved Passed: 19

## Case Results

| Case | Source | Baseline Recall | Evolved Recall | Evolved Passed |
|---|---|---:|---:|---:|
| oasst1-1acc7539f720 | OASST1 | 0.00% | 100.00% | yes |
| oasst1-9c34cc82c98d | OASST1 | 0.00% | 100.00% | yes |
| oasst1-53f739d38f4a | OASST1 | 0.00% | 100.00% | yes |
| oasst1-28c136a7b733 | OASST1 | 0.00% | 100.00% | yes |
| oasst1-6016e045496e | OASST1 | 0.00% | 100.00% | yes |
| oasst1-32a392d07371 | OASST1 | 0.00% | 100.00% | yes |
| oasst1-9baa4b6a737e | OASST1 | 0.00% | 100.00% | yes |
| oasst1-231697c6b0e1 | OASST1 | 0.00% | 100.00% | yes |
| oasst1-b501672d0d73 | OASST1 | 0.00% | 100.00% | yes |
| oasst1-46ab82b926ca | OASST1 | 0.00% | 100.00% | yes |
| oasst1-b60273bee528 | OASST1 | 0.00% | 100.00% | yes |
| oasst1-ee6c55745efa | OASST1 | 0.00% | 100.00% | yes |
| oasst1-e579afc2f8bb | OASST1 | 0.00% | 100.00% | yes |
| oasst1-445438beb955 | OASST1 | 0.00% | 100.00% | yes |
| oasst1-89d4423d1c89 | OASST1 | 0.00% | 100.00% | yes |
| oasst1-b6fb810d6899 | OASST1 | 0.00% | 100.00% | yes |
| oasst1-8071e3dbaa72 | OASST1 | 0.00% | 100.00% | yes |
| oasst1-0fd2c3ae66d7 | OASST1 | 0.00% | 100.00% | yes |
| oasst1-15560dde8fdc | OASST1 | 0.00% | 100.00% | yes |

## Source References

| Source | Task Family | Reference |
|---|---|---|
| OASST1 | code-task-with-follow-up | https://huggingface.co/datasets/OpenAssistant/oasst1 |

## Limitations

- This does not execute a forked agent or run repository tests.
- The baseline and evolved SOPs are fixed strings used for deterministic comparison.
- The next stronger benchmark should replay real tasks in a sandboxed fork-agent runner.
