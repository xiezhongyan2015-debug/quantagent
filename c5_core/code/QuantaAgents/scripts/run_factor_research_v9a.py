"""Run the independent V9A factor research workflow."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quanta_agents.factor_research.cli import main

if __name__ == "__main__":
    main()
