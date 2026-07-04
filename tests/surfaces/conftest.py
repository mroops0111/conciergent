import typing

from conciergent import TurnResult


class EchoAgent:
    # Stands in for ChatRunner across the surface webhook tests, recording each call and echoing the input back.
    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.bootstrapped: list[str] = []
        self.bootstrap_result = False

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
    ) -> TurnResult:
        self.inputs.append(user_input)
        return TurnResult(output=f'echo {user_input}', history=[{'seen': user_input}])
