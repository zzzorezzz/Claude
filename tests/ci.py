"""The test suite as the build runs it: if anything hangs (a torrent test whose
peers never connect on a busy build machine), every thread's stack is printed
and the run stops after a few minutes instead of waiting forever; a run that
fails is tried once more, for the tests that depend on timing.

    python -m tests.ci
"""

import faulthandler
import sys
import unittest

HANG_AFTER = 8 * 60      # seconds: the whole suite takes 2-5 minutes


def run(verbose):
    faulthandler.dump_traceback_later(HANG_AFTER, exit=True)
    try:
        suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
        return unittest.TextTestRunner(verbosity=2 if verbose else 1).run(suite).wasSuccessful()
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    verbose = "-q" not in sys.argv
    if run(verbose):
        sys.exit(0)
    print("\nSome tests failed - running the suite once more (some depend on timing).\n", flush=True)
    sys.exit(0 if run(verbose) else 1)
