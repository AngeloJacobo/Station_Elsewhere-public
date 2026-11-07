"""Bind sibling Lost NEET helpers to Station Elsewhere's own config and data."""
import importlib.util
import sys

from .config import ROOT


def load(name):
    source = ROOT.parent / "StoryCraft" / "app" / f"{name}.py"
    if not source.is_file():
        return None
    qualified = f"{__package__}.{name}"
    spec = importlib.util.spec_from_file_location(qualified, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


facebook = load("facebook")
storage = load("storage")
