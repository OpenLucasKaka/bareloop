"""
Compatibility shim for the legacy `mian.py` spelling.
Please prefer importing or running `bareloop.main`.
"""
import sys

from bareloop import main

# Alias this module to main in sys.modules so monkeypatching works identically
sys.modules[__name__] = main
