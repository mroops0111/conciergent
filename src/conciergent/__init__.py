import importlib
import typing


if typing.TYPE_CHECKING:
    from conciergent.app import App
    from conciergent.chat_context import ChatContext, DirectChatContext, GroupChatContext
    from conciergent.config import AppConfig
    from conciergent.identity import ChatSurface, make_principal, parse_principal
    from conciergent.reply import Card, Carousel, Fact, Link, Reply, ReplySurface, Section, Suggestion
    from conciergent.runtime import OAuthBridge, PendingApproval, StatefulOAuthBridge, TurnResult
    from conciergent.store.credential import CredentialStore
    from conciergent.store.message import MessageStore
    from conciergent.turn import run_turn


# Each name loads its module on first use, so a command that needs none of them starts without the agent stack.
# One such command is a push handed to a serving app over its control socket.
_MODULES = {
    'App': 'conciergent.app',
    'AppConfig': 'conciergent.config',
    'Card': 'conciergent.reply',
    'Carousel': 'conciergent.reply',
    'ChatContext': 'conciergent.chat_context',
    'ChatSurface': 'conciergent.identity',
    'CredentialStore': 'conciergent.store.credential',
    'DirectChatContext': 'conciergent.chat_context',
    'Fact': 'conciergent.reply',
    'GroupChatContext': 'conciergent.chat_context',
    'Link': 'conciergent.reply',
    'MessageStore': 'conciergent.store.message',
    'OAuthBridge': 'conciergent.runtime',
    'PendingApproval': 'conciergent.runtime',
    'Reply': 'conciergent.reply',
    'ReplySurface': 'conciergent.reply',
    'Section': 'conciergent.reply',
    'StatefulOAuthBridge': 'conciergent.runtime',
    'Suggestion': 'conciergent.reply',
    'TurnResult': 'conciergent.runtime',
    'make_principal': 'conciergent.identity',
    'parse_principal': 'conciergent.identity',
    'run_turn': 'conciergent.turn',
}


def __getattr__(name: str) -> typing.Any:
    module = _MODULES.get(name)
    if module is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


__all__ = [
    'App',
    'AppConfig',
    'Card',
    'Carousel',
    'ChatContext',
    'ChatSurface',
    'CredentialStore',
    'DirectChatContext',
    'Fact',
    'GroupChatContext',
    'Link',
    'MessageStore',
    'OAuthBridge',
    'PendingApproval',
    'Reply',
    'ReplySurface',
    'Section',
    'StatefulOAuthBridge',
    'Suggestion',
    'TurnResult',
    'make_principal',
    'parse_principal',
    'run_turn',
]
