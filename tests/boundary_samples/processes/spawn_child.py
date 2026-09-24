"""Boundary sample: Spawns a child process and sleeps indefinitely."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Spawn a child process and sleep.")
    parser.add_argument("--pid-file", required=True, help="File to record PIDs.")
    parser.add_argument("--is-child", action="store_true", help="Flag indicating child process.")
    args = parser.parse_args()

    pid_file = Path(args.pid_file)
    role = "child" if args.is_child else "parent"
    with pid_file.open("a", encoding="utf-8") as f:
        f.write(f"{role}:{os.getpid()}\n")
        f.flush()

    if not args.is_child:
        subprocess.Popen([sys.executable, __file__, "--pid-file", str(pid_file), "--is-child"])

    while True:
        time.sleep(0.05)


if __name__ == "__main__":
    main()
