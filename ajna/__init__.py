"""Ajna - a fast, dependency-free coding agent for the terminal.

Kept deliberately import-light: nothing heavy at package import time so that
`python -m ajna` reaches a prompt in well under a second.
"""

__version__ = "0.1.0"
NAME = "ajna"

__all__ = ["__version__", "NAME"]
