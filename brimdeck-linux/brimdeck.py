#!/usr/bin/env python3
"""BrimDeck for Linux — a Dynamic Island at the top of the screen."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brimdeck.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
