"""`python -m gharbasket_training.jobs.eval` on the Actions runner: the evaluation harness with
the protocol's seeds, pushing the rows to the staging API (`--push`)."""

import sys

from gharbasket_training.eval.__main__ import main

if __name__ == "__main__":
    sys.exit(main([*sys.argv[1:], "--push"]))
