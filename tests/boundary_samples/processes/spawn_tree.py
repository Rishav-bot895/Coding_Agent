"""Boundary sample: Spawns a multi-level process tree (root -> child -> grandchild)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Spawn a multi-level process tree.")
    parser.add_argument("--level", type=int, default=0, help="Current tree depth level (0=root, 1=child, 2=grandchild).")
    parser.add_argument("--pid-file", required=True, help="File to record PIDs.")
    args = parser.parse_args()

    pid_file = Path(args.pid_file)
    with pid_file.open("a", encoding="utf-8") as f:
        f.write(f"L{args.level}:{os.getpid()}\n")
        f.flush()

    if args.level < 2:
        # Spawn next level
        subprocess.Popen(
            [sys.executable, __file__, "--level", str(args.level + 1), "--pid-file", str(pid_file)]
        )

    # Sleep indefinitely
    while True:
        time.sleep(0.05)


if __name__ == "__main__":
    main()
