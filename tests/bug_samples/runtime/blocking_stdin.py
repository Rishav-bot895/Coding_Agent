"""Runtime fixture: blocks indefinitely reading from standard input."""

import sys

# Attempt to read from stdin
line = sys.stdin.readline()
sys.stdout.write(f"Read: {line}\n")
