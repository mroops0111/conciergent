"""conciergent — give your MCP tools a chat face.

A concierge and agent for chat surfaces: front your MCP tools to users over
Slack, LINE and more, with in-chat OAuth, human-in-the-loop approval, and a
surface-agnostic structured reply model.

This exposes the surface- and agent-agnostic core. Concrete surfaces and the
batteries-included agent are layered on top of these interfaces.
"""

from .identity import ChatSurface, make_principal, parse_principal
from .reply import Card, Carousel, Link, Reply, ReplySurface, Section, Suggestion
from .runtime import AgentResult, ChatAgent, OAuthBridge, PendingApproval, run_turn
from .stores import MemoryStore, Store


__version__ = '0.0.1'

__all__ = [
    'AgentResult',
    'Card',
    'Carousel',
    'ChatAgent',
    'ChatSurface',
    'Link',
    'MemoryStore',
    'OAuthBridge',
    'PendingApproval',
    'Reply',
    'ReplySurface',
    'Section',
    'Store',
    'Suggestion',
    'make_principal',
    'parse_principal',
    'run_turn',
]
