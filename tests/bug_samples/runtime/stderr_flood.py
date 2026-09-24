"""Runtime fixture: produces massive stderr flood exceeding 512 KB."""

import sys

chunk = "E" * 1024 + "\n"
# Write 2 MB of output
for _ in range(2048):
    sys.stderr.write(chunk)
    sys.stderr.flush()
