#!/usr/bin/env python3
"""Feather Dash -- entry point.

Kept deliberately thin: python-for-android launches this file directly, so it
must do nothing that could fail before the real application has a chance to
report an error.
"""
from __future__ import annotations

import sys


def main() -> int:
    from featherdash.app import main as run

    return run()


if __name__ == "__main__":
    sys.exit(main())
