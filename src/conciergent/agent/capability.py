import abc

import fastapi
from pydantic_ai.capabilities import AbstractCapability

from conciergent.agent.deps import AgentDeps


class Capability(AbstractCapability[AgentDeps], abc.ABC):
    """An opt-in agent feature, bringing its tools and hooks to the agent and any HTTP routes it needs to the app.

    A new feature subclasses this, so neither the runner nor the app changes to carry it.
    """

    def build_routers(self) -> list[fastapi.APIRouter]:
        """The routers the app mounts for this capability, none by default."""
        return []
