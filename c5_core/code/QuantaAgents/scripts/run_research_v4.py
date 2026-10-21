"""V4 controller entry, sharing the existing ledger, tools and execution kernel.

Create requires an explicit work plan. Old V3 frozen studies keep their identity.
"""
from run_research_v3 import main


if __name__ == '__main__':
    main(require_v4=True)
