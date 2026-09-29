"""Thin wrapper: `python scripts/demo.py [--inproc]`. See aiip/demo.py."""

import sys

from aiip.demo import main

sys.exit(main())
