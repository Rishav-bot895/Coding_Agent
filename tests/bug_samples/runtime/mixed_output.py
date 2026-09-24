"""Runtime fixture: produces interleaved stdout and stderr output."""

import sys

for i in range(100):
    sys.stdout.write(f"out {i}\n")
    sys.stderr.write(f"err {i}\n")
    sys.stdout.flush()
    sys.stderr.flush()
