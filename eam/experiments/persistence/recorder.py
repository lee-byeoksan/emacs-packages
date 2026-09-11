"""Compatibility entry point for the product persistence implementation."""
from pathlib import Path
import runpy
import sys
_product = Path(__file__).resolve().parents[2] / 'bridge' / 'persistence'
sys.path.insert(0, str(_product))
globals().update(runpy.run_path(str(_product / 'recorder.py'), run_name=__name__))
