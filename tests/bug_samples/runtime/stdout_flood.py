"""Runtime fixture: produces massive stdout flood exceeding 512 KB."""

import sys

chunk = "X" * 1024 + "\n"
# Write 2 MB of output
for _ in range(2048):
    sys.stdout.write(chunk)
    sys.stdout.flush()
