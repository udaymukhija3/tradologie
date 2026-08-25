"""Business tools exposed to the voice engines.

The submodule imports below are load-bearing side effects: each module calls
@registry.register at import time, so dropping them as "unused" silently
empties the tool registry.
"""

from . import distributors, enquiries, support  # noqa: F401
from .registry import registry

__all__ = ["registry"]
