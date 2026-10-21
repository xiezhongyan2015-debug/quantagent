"""V7 application entry point."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quanta_agents.meta_v7.cli import main
if __name__ == "__main__":
    main()
