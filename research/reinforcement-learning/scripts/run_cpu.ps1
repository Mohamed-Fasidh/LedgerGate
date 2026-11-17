$ErrorActionPreference = "Stop"
python -m pip install -e ".[test]"
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "RL test suite failed" }
python -m rl_research.run_all --only all --device cpu --output outputs --threads 1
if ($LASTEXITCODE -ne 0) { throw "RL experiment run failed" }
