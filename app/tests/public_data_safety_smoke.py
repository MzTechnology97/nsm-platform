"""Public-repository fixture safety regression guard."""

import subprocess
import sys


def main():
    subprocess.run(
        [sys.executable, "../scripts/check-public-data-safety.py"],
        check=True,
    )
    print("Public repository data-safety smoke passed")


if __name__ == "__main__":
    main()
