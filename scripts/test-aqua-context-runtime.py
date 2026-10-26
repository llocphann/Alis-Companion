#!/usr/bin/env python3
"""Exercise context opt-in in the existing real package/lifecycle fixture."""
import os
from pathlib import Path
import runpy

os.environ['ALIS_COMPANION_TEST_CONTEXT']='1'
runpy.run_path(str(Path(__file__).with_name('test-host-runtime.py')),run_name='__main__')
