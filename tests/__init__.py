"""Tests never touch the real index or settings.

Each test module points GLIMPSE_DATA at its own temp folder before importing glimpse. When several modules run in
one process (e.g. `python -m unittest discover`), the first glimpse import fixes the data folder for all of them,
so it is set here, before any of them, to a throwaway folder.
"""
import os
import tempfile

if not os.environ.get("GLIMPSE_DATA"):
    os.environ["GLIMPSE_DATA"] = os.path.join(tempfile.mkdtemp(prefix="glimpse_tests_"), "data")
