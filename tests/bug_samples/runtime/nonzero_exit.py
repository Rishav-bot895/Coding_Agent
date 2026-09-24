"""Runtime fixture: failure with non-zero exit code."""

import sys

sys.stderr.write("Fatal runtime error occurred\n")
sys.exit(42)
