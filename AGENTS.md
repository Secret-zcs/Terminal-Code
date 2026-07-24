# Repository Guidelines

## Project Structure & Module Organization

MewCode is a Python 3.11+ terminal AI coding assistant. Core source lives in `mewcode/`. Important modules include `agent.py` for the main loop, `app.py` for the Textual UI, `client.py` for LLM providers, and `prompts.py` for system context. Feature packages are grouped by responsibility: `tools/`, `permissions/`, `context/`, `checkpoint/`, `evolution/`, `skills/`, `commands/`, `agents/`, `teams/`, `mcp/`, `hooks/`, and `worktree/`. Tests live in `tests/`, documentation in `docs/`, and utility scripts in `scripts/`. Local runtime state such as sessions, checkpoints, permissions, and project skills is stored under `.mewcode/` and should not be committed.

## Build, Test, and Development Commands

- `python3 -m venv .venv && source .venv/bin/activate`: create and activate a local environment.
- `pip install -e .`: install the package in editable mode.
- `pip install pytest pytest-asyncio`: install test dependencies if not using the dev dependency group.
- `mewcode`: run the interactive terminal UI.
- `mewcode -p "summarize README.md"`: run one non-interactive prompt.
- `PYTHONPATH=. pytest tests/test_evolution.py -q`: run a focused test module.
- `PYTHONPATH=. pytest -q`: run the full suite.

## Coding Style & Naming Conventions

Use 4-space indentation, type hints, and small modules with clear responsibilities. Keep public command names and slash-command handlers explicit, for example `handle_evolve` in `mewcode/commands/handlers/evolve.py`. Prefer `snake_case` for functions, variables, and files; use `PascalCase` for classes. Follow existing patterns before introducing new abstractions. Avoid committing generated caches such as `__pycache__/`, `.pytest_cache/`, `.venv/`, or `.mewcode/` runtime data.

## Testing Guidelines

Tests use `pytest` and `pytest-asyncio`. Place tests in `tests/` and name files `test_*.py`; name test methods by behavior, e.g. `test_run_execution_eval_creates_sandbox_artifacts`. Add regression tests before changing behavior, especially for permissions, checkpoint/rewind, context compression, skills, and self-evolution flows. For broad changes, run the focused module plus the related aggregate command shown above.

## Commit & Pull Request Guidelines

Recent commits use short, imperative Chinese summaries, for example `为 skill 执行评估增加沙盒产物`. Keep commits focused and avoid staging unrelated local edits. PRs should describe the user-visible change, list key files touched, include verification commands and results, and call out known unrelated failures such as legacy tests that conflict with current safety policy.

## Security & Configuration Tips

Do not commit API keys, local provider config, session logs, checkpoints, or permission files. Configuration is loaded from `~/.mewcode/config.yaml`, project `.mewcode/config.yaml`, and `.mewcode/config.local.yaml`; prefer environment variables such as `${ANTHROPIC_API_KEY}` or `${OPENAI_API_KEY}` for secrets.
