#!/usr/bin/env python
"""Run without installing:  python jobhunt.py <command>"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jobhunt.cli import main
if __name__ == "__main__":
    main()
