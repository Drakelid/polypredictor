"""Model package for PolyPredictor.

This package exposes high-level model utilities, classifiers, and
types used throughout the PolyPredictor project. It is structured as
a nested module so that submodules can be imported with concise
names, for example::

    from packages.model import classifier

or::

    from packages.model import llm_classifier

The presence of this file marks the directory as a Python package.
"""

from .src.model import classifier  # noqa: F401
from .src.model import llm_classifier  # noqa: F401
from .src.model.types import MarketType  # noqa: F401
