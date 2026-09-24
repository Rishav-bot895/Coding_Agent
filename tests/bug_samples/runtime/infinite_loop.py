"""Runtime fixture: infinite loop that never terminates on its own."""

import time

while True:
    time.sleep(0.01)
