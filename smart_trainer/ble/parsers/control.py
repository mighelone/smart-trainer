"""Control point responses shared by FTMS and Cycling Power: result codes and texts.

Pure functions, free of side effects, so they can be unit tested with captured payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ResultCode(IntEnum):
    """FTMS / Cycling Power control point result codes."""

    SUCCESS = 0x01
    NOT_SUPPORTED = 0x02
    INVALID_PARAMETER = 0x03
    FAILED = 0x04
    CONTROL_NOT_PERMITTED = 0x05


@dataclass(slots=True)
class ControlResponse:
    """Response to an FTMS control point command.

    Attributes:
        request_op (int): Op code of the command being answered.
        result (int): Result code, see ``ResultCode``.
        params (bytes): Response parameters following the result code, if any.
    """

    request_op: int
    result: int
    params: bytes = b""

    @property
    def ok(self) -> bool:
        """Whether the command succeeded.

        Returns:
            bool: ``True`` if the result code is ``ResultCode.SUCCESS``.
        """
        return self.result == ResultCode.SUCCESS


RESULT_TEXT: dict[int, str] = {
    ResultCode.SUCCESS: "success",
    ResultCode.NOT_SUPPORTED: "not supported",
    ResultCode.INVALID_PARAMETER: "invalid parameter",
    ResultCode.FAILED: "operation failed",
    ResultCode.CONTROL_NOT_PERMITTED: "control not permitted",
}


def result_text(code: int) -> str:
    """Describe an FTMS / Cycling Power control point result code.

    Args:
        code (int): Result code from a control point response.

    Returns:
        str: Human-readable description.
    """
    return RESULT_TEXT.get(code, f"error 0x{code:02x}")
