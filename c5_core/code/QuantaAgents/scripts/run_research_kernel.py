"""Run the persistent program layer from the existing project environment."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quanta_agents.research_kernel.cli import main

if __name__ == "__main__":
    main()
