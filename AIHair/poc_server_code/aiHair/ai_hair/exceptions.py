"""aiHair adapter exceptions.

Each carries its engine result code as args[1], which server.py reports as
the job's error code.
"""

from __future__ import annotations

from engine_result_code import (
    RESULT_ERROR_C2PA_KEY_MISSING,
    RESULT_ERROR_C2PA_SIGN_FAILED,
    RESULT_ERROR_C2PA_UNSUPPORTED,
    RESULT_INVALID_PARAMETER,
    RESULT_UNKNOWN_INTERNAL_ERROR,
)


class AiHairError(RuntimeError):
    def __init__(self, message: str, code: str = RESULT_UNKNOWN_INTERNAL_ERROR):
        super().__init__(message, code)

    @property
    def code(self) -> str:
        return self.args[1]


class RetryableError(AiHairError):
    pass


class C2PAKeyMaterialError(AiHairError):
    def __init__(self, message: str, code: str = RESULT_ERROR_C2PA_KEY_MISSING):
        super().__init__(message, code)


class C2PASignFailedError(AiHairError):
    def __init__(self, message: str, code: str = RESULT_ERROR_C2PA_SIGN_FAILED):
        super().__init__(message, code)


class C2PAUnsupportedError(AiHairError):
    def __init__(self, message: str, code: str = RESULT_ERROR_C2PA_UNSUPPORTED):
        super().__init__(message, code)


class NonRetryableError(AiHairError):
    def __init__(self, message: str, code: str = RESULT_INVALID_PARAMETER):
        super().__init__(message, code)
