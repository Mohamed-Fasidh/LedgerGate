#!/usr/bin/env sh
set -eu
python -m pip install -e ".[test]"
python -m pytest -q
python -m rl_research.run_all --only all --device cpu --output outputs --threads 1
