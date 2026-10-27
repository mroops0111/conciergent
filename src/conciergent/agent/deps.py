import dataclasses

from conciergent.chat_context import ChatContext
from conciergent.i18n.lang import Lang
from conciergent.reply import ReplySurface


@dataclasses.dataclass
class AgentDeps:
    """Per-turn context the agent's instructions, tools, and capabilities read, carried through pydantic-ai deps."""

    surface: ReplySurface | None
    lang: Lang | None
    principal: str
    chat_context: ChatContext
    # A tool run may set this, e.g. the sign-out tool, to have the turn clear the stored history instead of appending.
    invalidate_history: bool = False
