import typing

from conciergent import TurnResult


class EchoAgent:
    # Stands in for ChatRunner across the surface webhook tests, recording each call and echoing the input back.
    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.bootstrapped: list[str] = []
        self.bootstrap_result = False
        self.groups_supported = True
        # One entry per run, the keyword arguments a test may want to inspect beyond the input.
        self.calls: list[dict[str, typing.Any]] = []

    async def supports_groups(self) -> bool:
        return self.groups_supported

    async def bootstrap(self, principal: str, *, bridge: typing.Any = None) -> bool:
        self.bootstrapped.append(principal)
        return self.bootstrap_result

    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[typing.Any],
        pending_approval: dict[str, typing.Any] | None,
        bridge: typing.Any = None,
        surface: typing.Any = None,
        speaker: str | None = None,
    ) -> TurnResult:
        self.inputs.append(user_input)
        self.calls.append(
            {
                'principal': principal,
                'bridge': bridge,
                'speaker': speaker,
                'pending_approval': pending_approval,
                'history': history,
            }
        )
        return TurnResult(output=f'echo {user_input}', history=[{'seen': user_input}])
