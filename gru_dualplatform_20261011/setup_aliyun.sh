#!/bin/bash
# Execute only after explicit approval to install these research dependencies.
set -euo pipefail
mkdir -p /root/gru_research_20261011
cd /root/gru_research_20261011
apt-get update
apt-get install -y --no-install-recommends python3-venv libgomp1
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: torch==2.9.1 --index-url https://download.pytorch.org/whl/cu128
.venv/bin/python -m pip install --only-binary=:all: numpy==2.2.6 pandas==2.3.3 scipy==1.15.3 pyarrow==22.0.0 lightgbm==4.6.0 --index-url https://pypi.org/simple
.venv/bin/python -m pip freeze > environment_versions.txt
.venv/bin/python - <<'PY'
import torch,lightgbm,numpy,pandas,scipy,pyarrow
print(torch.__version__, torch.cuda.is_available())
assert torch.cuda.is_available()
print(torch.cuda.get_device_name(),torch.cuda.get_device_properties(0).total_memory)
PY
