"""Backward-compatible alias for teammate module."""
import sys

from bareloop.tools.adapters import teammate

# Point module directly to teammate so monkeypatches on teamate affect teammate
sys.modules[__name__] = teammate
