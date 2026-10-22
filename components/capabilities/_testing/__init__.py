"""Test helpers shared by the capability packages' suites. Not a package of the workspace.

pytest puts this directory's parent on ``sys.path`` (it holds the workspace ``conftest.py``,
which belongs to no package), so a suite imports ``from _testing.layers import ...``.
"""
