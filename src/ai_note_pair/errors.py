"""User-facing application errors."""


class AiNotePairError(Exception):
    """Actionable failure that should stop a CLI command."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code
