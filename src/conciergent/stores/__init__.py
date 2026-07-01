"""State-store interface and built-in backends."""

from .base import Store
from .memory import MemoryStore


__all__ = ['MemoryStore', 'Store']
