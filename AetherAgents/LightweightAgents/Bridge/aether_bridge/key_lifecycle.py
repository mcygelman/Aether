"""Moved to aether.keys.key_lifecycle (Modularization MOD-03); this alias keeps the old import path working."""
# aether-module-alias
import sys as _sys

if __name__ == "__main__":
    import runpy as _runpy
    _runpy.run_module("aether.keys.key_lifecycle", run_name="__main__", alter_sys=True)
else:
    from importlib import import_module as _import_module
    _sys.modules[__name__] = _import_module("aether.keys.key_lifecycle")
