"""Unit tests for Python traceback parsing and error signature extraction."""

from __future__ import annotations

import builtins
from unittest.mock import patch

from localdev.languages.python.traceback_parser import (
    normalize_frame_path,
    parse_traceback,
)
from localdev.schemas import ErrorSignature, TracebackFrame


def test_parse_single_exception() -> None:
    stderr = """Traceback (most recent call last):
  File "D:\\Project\\calc.py", line 12, in compute
    return 10 / divisor
           ~~~^~~~~~~~~
ZeroDivisionError: division by zero
"""
    target = "D:\\Project\\calc.py"
    frames, sig = parse_traceback(stderr, target_path=target)

    assert len(frames) == 1
    assert frames[0] == TracebackFrame(
        file_path="D:\\Project\\calc.py",
        line_number=12,
        function_name="compute",
        code_line="return 10 / divisor",
        is_target=True,
    )
    assert sig is not None
    assert sig == ErrorSignature(
        exception_type="ZeroDivisionError",
        normalized_message="division by zero",
        top_target_file="D:\\Project\\calc.py",
        top_target_line=12,
    )


def test_parse_chained_exception_explicit() -> None:
    stderr = """Traceback (most recent call last):
  File "D:\\Project\\app.py", line 5, in load_data
    raise KeyError("missing key")
KeyError: 'missing key'

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "D:\\Project\\app.py", line 10, in main
    load_data()
  File "D:\\Project\\app.py", line 7, in load_data
    raise RuntimeError("Failed to load") from exc
RuntimeError: Failed to load
"""
    target = "D:\\Project\\app.py"
    frames, sig = parse_traceback(stderr, target_path=target)

    assert len(frames) == 3
    assert sig is not None
    assert sig.exception_type == "RuntimeError"
    assert sig.normalized_message == "Failed to load"
    assert sig.top_target_file == "D:\\Project\\app.py"
    assert sig.top_target_line == 7


def test_parse_chained_exception_implicit() -> None:
    stderr = """Traceback (most recent call last):
  File "D:\\Project\\app.py", line 4, in handle
    raise IndexError("list index out of range")
IndexError: list index out of range

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "D:\\Project\\app.py", line 8, in main
    handle()
  File "D:\\Project\\app.py", line 6, in handle
    raise ValueError("invalid operation")
ValueError: invalid operation
"""
    target = "D:\\Project\\app.py"
    frames, sig = parse_traceback(stderr, target_path=target)

    assert len(frames) == 3
    assert sig is not None
    assert sig.exception_type == "ValueError"
    assert sig.normalized_message == "invalid operation"
    assert sig.top_target_line == 6


def test_parse_runtime_syntax_error() -> None:
    stderr = """  File "D:\\Project\\bad_syntax.py", line 4
    if True
           ^
SyntaxError: expected ':'
"""
    target = "D:\\Project\\bad_syntax.py"
    frames, sig = parse_traceback(stderr, target_path=target)

    assert len(frames) == 1
    assert frames[0].file_path == "D:\\Project\\bad_syntax.py"
    assert frames[0].line_number == 4
    assert frames[0].code_line == "if True"
    assert frames[0].is_target is True

    assert sig is not None
    assert sig.exception_type == "SyntaxError"
    assert sig.normalized_message == "expected ':'"
    assert sig.top_target_file == "D:\\Project\\bad_syntax.py"
    assert sig.top_target_line == 4


def test_parse_recursion_error() -> None:
    stderr = """Traceback (most recent call last):
  File "D:\\Project\\recurse.py", line 2, in recurse
    return recurse()
  [Previous line repeated 996 more times]
RecursionError: maximum recursion depth exceeded
"""
    target = "D:\\Project\\recurse.py"
    frames, sig = parse_traceback(stderr, target_path=target)

    assert len(frames) == 1
    assert frames[0].line_number == 2
    assert frames[0].code_line == "return recurse()"
    assert sig is not None
    assert sig.exception_type == "RecursionError"
    assert "maximum recursion depth exceeded" in sig.normalized_message


def test_external_library_frames_zero_file_reads() -> None:
    stderr = """Traceback (most recent call last):
  File "D:\\Project\\app.py", line 15, in fetch_url
    urllib.request.urlopen("https://example.com")
  File "C:\\Python312\\Lib\\urllib\\request.py", line 215, in urlopen
    return opener.open(url, data, timeout)
  File "C:\\Python312\\Lib\\urllib\\request.py", line 521, in open
    response = meth(req, response)
  File "C:\\Python312\\Lib\\http\\client.py", line 1340, in getresponse
    raise RemoteDisconnected("Remote end closed connection without response")
http.client.RemoteDisconnected: Remote end closed connection without response
"""
    target = "D:\\Project\\app.py"

    original_open = builtins.open
    open_called_paths: list[str] = []

    def spy_open(file: object, *args: object, **kwargs: object) -> object:
        open_called_paths.append(str(file))
        return original_open(file, *args, **kwargs)  # type: ignore[call-overload]

    with patch("builtins.open", side_effect=spy_open):
        frames, sig = parse_traceback(stderr, target_path=target)

    # Invariant: External files must never be opened or inspected during parsing
    assert len(open_called_paths) == 0

    assert len(frames) == 4
    # Frame 0 is target
    assert frames[0].file_path == "D:\\Project\\app.py"
    assert frames[0].is_target is True
    assert frames[0].line_number == 15

    # Frames 1-3 are external
    assert frames[1].is_target is False
    assert "request.py" in frames[1].file_path
    assert frames[2].is_target is False
    assert frames[3].is_target is False
    assert "client.py" in frames[3].file_path

    # Error signature must highlight top target frame (the user code trigger)
    assert sig is not None
    assert sig.exception_type == "http.client.RemoteDisconnected"
    assert "Remote end closed connection without response" in sig.normalized_message
    assert sig.top_target_file == "D:\\Project\\app.py"
    assert sig.top_target_line == 15


def test_session_copy_path_normalization() -> None:
    session_copy = "C:\\Temp\\localdev\\session_abc123\\session_target.py"
    target = "D:\\MyWork\\solution.py"

    norm_path, is_target = normalize_frame_path(
        session_copy,
        target_path=target,
        session_target_path=session_copy,
    )
    assert norm_path == target
    assert is_target is True

    stderr = f"""Traceback (most recent call last):
  File "{session_copy}", line 8, in run
    raise ValueError("bad input")
ValueError: bad input
"""
    frames, sig = parse_traceback(
        stderr,
        target_path=target,
        session_target_path=session_copy,
    )
    assert len(frames) == 1
    assert frames[0].file_path == target
    assert frames[0].is_target is True
    assert sig is not None
    assert sig.top_target_file == target
    assert sig.top_target_line == 8


def test_empty_and_clean_stderr() -> None:
    target = "D:\\Project\\clean.py"
    frames, sig = parse_traceback("", target_path=target)
    assert frames == []
    assert sig is None

    frames, sig = parse_traceback("All tests passed.\nDone in 0.05s\n", target_path=target)
    assert frames == []
    assert sig is None


def test_multiline_exception_message() -> None:
    stderr = """Traceback (most recent call last):
  File "D:\\Project\\validate.py", line 10, in validate
    raise ValueError("Multi-line validation error:\\n  field 'a' missing\\n  field 'b' invalid")
ValueError: Multi-line validation error:
  field 'a' missing
  field 'b' invalid
"""
    target = "D:\\Project\\validate.py"
    frames, sig = parse_traceback(stderr, target_path=target)

    assert len(frames) == 1
    assert sig is not None
    assert sig.exception_type == "ValueError"
    assert "Multi-line validation error:" in sig.normalized_message
    assert "field 'a' missing" in sig.normalized_message
    assert "field 'b' invalid" in sig.normalized_message
