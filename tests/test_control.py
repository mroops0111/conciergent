import asyncio
import os
import pathlib
import socket
import stat
import subprocess
import sys

import pytest

from conciergent.control import ControlConnectionLostError, ControlServer, ControlUnavailableError, request_push
from conciergent.push_outcome import PushOutcome, UnknownPushError


class _RecordingPushes:
    def __init__(self, outcome: PushOutcome = PushOutcome.delivered) -> None:
        self.outcome = outcome
        self.names: list[str] = []

    async def run(self, name: str) -> PushOutcome:
        if name != 'daily-digest':
            raise UnknownPushError(f'no push named {name!r}')
        self.names.append(name)
        return self.outcome


async def test_a_handed_push_runs_in_the_server_and_reports_its_outcome(control_socket: str):
    pushes = _RecordingPushes(PushOutcome.skipped)

    async with ControlServer(control_socket, pushes.run):
        outcome = await asyncio.to_thread(request_push, control_socket, 'daily-digest')

    assert outcome is PushOutcome.skipped
    assert pushes.names == ['daily-digest']


async def test_an_unknown_push_is_reported_back_by_name(control_socket: str):
    async with ControlServer(control_socket, _RecordingPushes().run):
        with pytest.raises(UnknownPushError, match="no push named 'weekly'"):
            await asyncio.to_thread(request_push, control_socket, 'weekly')


async def test_a_push_that_dies_in_the_server_leaves_the_outcome_unknown(control_socket: str):
    async def broken(name: str) -> PushOutcome:
        raise RuntimeError('boom')

    async with ControlServer(control_socket, broken):
        with pytest.raises(ControlConnectionLostError):
            await asyncio.to_thread(request_push, control_socket, 'daily-digest')


async def test_the_socket_is_private_to_the_app_user_and_removed_at_shutdown(control_socket: str):
    async with ControlServer(control_socket, _RecordingPushes().run):
        mode = os.stat(control_socket).st_mode
        assert stat.S_ISSOCK(mode)
        assert stat.S_IMODE(mode) == 0o600

    assert not pathlib.Path(control_socket).exists()


def test_a_missing_socket_means_no_app_took_the_push(control_socket: str):
    with pytest.raises(ControlUnavailableError):
        request_push(control_socket, 'daily-digest')


async def test_a_socket_left_by_a_dead_app_is_unavailable_then_replaced(control_socket: str):
    # Binding and closing without unlinking leaves the file the way a killed app would.
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(control_socket)
    stale.close()

    with pytest.raises(ControlUnavailableError):
        request_push(control_socket, 'daily-digest')
    async with ControlServer(control_socket, _RecordingPushes().run):
        outcome = await asyncio.to_thread(request_push, control_socket, 'daily-digest')

    assert outcome is PushOutcome.delivered


async def test_a_second_app_refuses_a_socket_another_app_serves(control_socket: str):
    async with ControlServer(control_socket, _RecordingPushes().run):
        with pytest.raises(RuntimeError, match='another app already takes pushes'):
            async with ControlServer(control_socket, _RecordingPushes().run):
                pass


async def test_a_path_that_is_not_a_socket_is_refused(control_socket: str):
    pathlib.Path(control_socket).write_text('')

    with pytest.raises(RuntimeError, match='is not a socket'):
        async with ControlServer(control_socket, _RecordingPushes().run):
            pass


def test_the_push_command_imports_no_agent_code():
    probe = (
        'import sys, conciergent.cli; '
        "loaded = [m for m in ('conciergent.app', 'pydantic_ai', 'fastapi', 'pydantic') if m in sys.modules]; "
        'print(loaded)'
    )

    result = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, check=True)

    assert result.stdout.strip() == '[]'
