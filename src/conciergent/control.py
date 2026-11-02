import asyncio
import collections.abc
import contextlib
import json
import logging
import os
import pathlib
import socket
import stat
import types
import typing

from conciergent.push_outcome import PushOutcome, UnknownPushError


logger = logging.getLogger(__name__)

# Each request and each response is one JSON object on one line.
# A request names the push in its push field.
# A response carries the outcome field, or an error field set to unknown_push along with a message field.
_UNKNOWN_PUSH = 'unknown_push'


class ControlUnavailableError(ConnectionError):
    """Nothing answers at the control socket, so the push was never handed over and can run elsewhere."""


class ControlConnectionLostError(ConnectionError):
    """The serving app took the push, but the connection ended before it said how the push ended."""


def request_push(path: str, name: str) -> PushOutcome:
    """Hand the push called ``name`` to the app serving at the control socket ``path``, and wait for its outcome.

    It imports nothing from the agent, so the push command starts fast when an app is serving.
    """
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    with connection:
        try:
            connection.connect(path)
            connection.sendall(json.dumps({'push': name}).encode() + b'\n')
        except OSError as error:
            # The app cannot have read a whole request, so it never started the push.
            raise ControlUnavailableError(f'nothing answers at the control socket {path}') from error
        try:
            with connection.makefile('rb') as reader:
                line = reader.readline()
        except OSError as error:
            raise ControlConnectionLostError(f'the app at {path} stopped answering during push {name}') from error
    if not line:
        raise ControlConnectionLostError(f'the app at {path} closed the connection during push {name}')
    response = json.loads(line)
    if response.get('error') == _UNKNOWN_PUSH:
        raise UnknownPushError(response['message'])
    return PushOutcome(response['outcome'])


class ControlServer:
    """Listen on a Unix domain socket and run each push the push command hands over.

    Only processes on the same host or in the same container can connect.
    The socket's file mode lets only the app's own user connect.
    """

    def __init__(
        self, path: str, run_push: collections.abc.Callable[[str], collections.abc.Awaitable[PushOutcome]]
    ) -> None:
        self._path = pathlib.Path(path).expanduser()
        self._run_push = run_push
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> typing.Self:
        self._clear_stale_socket()
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=self._path)
        os.chmod(self._path, 0o600)
        logger.info('Taking pushes on the control socket %s', self._path)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        if self._server is not None:
            self._server.close()
            # This waits for a push still running, so a shutdown does not cut a reply off midway.
            await self._server.wait_closed()
        self._path.unlink(missing_ok=True)

    def _clear_stale_socket(self) -> None:
        try:
            mode = self._path.stat().st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(mode):
            raise RuntimeError(f'server.control_socket {self._path} exists and is not a socket')
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with probe:
            try:
                probe.connect(str(self._path))
            except ConnectionRefusedError:
                # A previous app died without removing it.
                self._path.unlink()
                return
        raise RuntimeError(f'another app already takes pushes on the control socket {self._path}')

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = json.loads(await reader.readline())
            name = request['push']
            try:
                outcome = await self._run_push(name)
            except UnknownPushError as error:
                response: dict[str, str] = {'error': _UNKNOWN_PUSH, 'message': str(error)}
            else:
                response = {'outcome': outcome.value}
            writer.write(json.dumps(response).encode() + b'\n')
            await writer.drain()
        except Exception:
            # The push command then exits saying the outcome is unknown, rather than guessing one.
            logger.exception('A control socket request failed')
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
