import sys

from agent import run_pipeline
from briefing import save_briefing
from logging_config import setup_logging


def main() -> None:
    setup_logging()

    briefing = run_pipeline()
    path = save_briefing(briefing)

    print(f"\nBriefing written to: {path}")
    print(f"Status: {briefing.status}")

    if briefing.errors:
        print("\nErrors:")
        for err in briefing.errors:
            print(f"  [{err.stage}] {err.message}")

    if briefing.status == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
