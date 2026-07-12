"""lore_cli — command-line dispatch for Lore.

Every verb is a typer app in a sibling ``*_cmd`` module, mounted by
``lore_cli.__main__``. Run ``lore --help`` for the current surface;
this docstring deliberately doesn't enumerate it, so it can't rot.
"""
