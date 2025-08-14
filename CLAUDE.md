# Claude Rules for this Project

## Python Environment Management
- NEVER use global pip install
- ALWAYS use `uv` for Python environment and dependency management
- Create virtual environments with `uv venv` before installing packages
- Install packages with `uv add` or `uv pip install` within the virtual environment