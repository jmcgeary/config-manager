# Claude Rules for this Project

## Python Environment Management
- NEVER use global pip install
- ALWAYS use `uv` for Python environment and dependency management
- Create virtual environments with `uv venv` before installing packages
- Install packages with `uv add` or `uv pip install` within the virtual environment

## Git Commit Policy
- NEVER commit changes unless explicitly asked by the user
- User wants to review diffs before committing
- When user asks for commits, do NOT include Claude attribution in commit messages