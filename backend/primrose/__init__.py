"""Additive PRIMROSE analyst-workbench services.

The package consumes the existing :mod:`kg_backend` and
``target_development`` public interfaces.  It deliberately does not own entity
extraction, graph review state, or graph reasoning.
"""

from .service import PrimroseWorkbench

__all__ = ["PrimroseWorkbench"]
