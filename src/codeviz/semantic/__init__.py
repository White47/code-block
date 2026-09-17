"""Framework-specific static recognition rules, separate from Python parsing."""

from .pytorch import PyTorchModelRule
from .rules import ConstructorMatch, ModelRule

__all__ = ["ConstructorMatch", "ModelRule", "PyTorchModelRule"]
