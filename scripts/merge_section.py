#!/usr/bin/env python3
"""Idempotently replace-or-append one Markdown ``##`` section in a document.

Reference implementation for the markdown-idempotent-section-merge skill:

- Heading scans are fence-aware: lines inside ``` or ~~~ fenced code blocks
  never count as headings or section boundaries.
- A section boundary is the next heading of level 1 or 2 (``#`` or ``##``
  followed by space/tab or end of line, up to 3 leading spaces). ``###``
  subheadings stay inside the section; an H1 ends it — a part boundary must
  never be swallowed into a replace.
- The merged block must contain exactly one H1/H2-level heading: its own
  first line, a plain column-0 ``## Heading``.
- Malformed input is refused (exit 2) instead of guessed at: duplicate
  copies of the heading in the target, extra headings in the block, an
  unclosed code fence in either, CR-only line endings, or a possible setext
  heading (``===`` / ``---`` underline) inside the replaced span.
- Applying the same merge twice leaves the file byte-identical
  (apply-twice-diff-zero). The target's line-ending style (LF or CRLF) and
  UTF-8 BOM are preserved; when only mixed line endings need normalizing,
  the action is reported as ``normalized``, not hidden.
- Changed content is flushed to an exclusive same-directory temporary file
  and installed with one atomic replace. Symbolic-link and multi-hard-link
  targets are refused because replacement would change their link semantics;
  Windows reparse points and non-regular files are refused before reading.
  Windows uses ``ReplaceFileW`` to preserve ACLs and file attributes; POSIX
  preserves owner/group, mode, and a bounded set of extended attributes.
  The target identity, metadata, and bytes are rechecked immediately before
  commit. This is a best-effort conflict check, not compare-and-swap: writers
  needing strict lost-update prevention must serialize externally. Creation
  of a previously missing target uses an atomic no-replace commit.

Usage:

    python merge_section.py TARGET BLOCK [--check]

The BLOCK file's first line is the exact ``## Heading`` line of the section
to replace (when TARGET already contains it outside code fences) or append
(when it does not). A missing TARGET is treated as an empty document, so
the section is appended and the file is created.

Exit codes: 0 = success. With --check (write nothing): 0 = TARGET is
already canonical, 1 = a merge (or line-ending normalization) would change
it. 2 = usage or validation error.
"""

import argparse
import errno
import os
import re
import secrets
import stat
import sys
from pathlib import Path

# A fence delimiter line: up to 3 leading spaces, then a run of 3+ backticks
# or 3+ tildes. Four or more leading spaces would be an indented code block,
# not a fence.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")

# A section boundary: a heading of level 1 or 2 — up to 3 leading spaces,
# then "#" or "##" followed by space, tab, or end of line (CommonMark's ATX
# rule, so a "##hashtag" paragraph line is not a boundary). This hardens the
# folk form ``^##[^#]``: it still excludes ``###``, and it additionally
# treats an H1 as a boundary instead of silently replacing across it.
_BOUNDARY_RE = re.compile(r"^ {0,3}#{1,2}(?:[ \t]|$)")

# The canonical block's own heading: a plain column-0 H2.
_H2_RE = re.compile(r"^##(?:[ \t]|$)")

# A possible setext underline: a run of "=" or "-" alone on a line. Under a
# paragraph line this is a real heading that the boundary scan above cannot
# see, so the merge refuses to replace across one.
_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")

_BOM = b"\xef\xbb\xbf"
_MAX_XATTR_COUNT = 256
_MAX_XATTR_BYTES = 1024 * 1024


class MergeError(ValueError):
    """Raised when the block or the target violates a merge invariant."""


class AtomicCommitError(MergeError):
    """Report a partial Windows commit without discarding recovery files."""

    def __init__(self, message, committed=False, recovered=False, artifacts=()):
        super().__init__(message)
        self.committed = committed
        self.recovered = recovered
        self.artifacts = tuple(artifacts)


def _fence_scan(lines):
    """Return ``(states, open_at_end)``.

    ``states`` maps each line to True when it belongs to a fenced code
    block; both delimiter lines count as inside. A backtick fence whose info
    string contains a backtick does not open (CommonMark). A fence closes
    only on a run of the same character, at least as long as the opener,
    with nothing else on the line; an unclosed fence runs to the end of the
    document (CommonMark behaviour), which ``open_at_end`` reports.
    """
    states = []
    open_char = ""
    open_len = 0
    for line in lines:
        match = _FENCE_RE.match(line)
        if not open_char:
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                open_char = match.group(1)[0]
                open_len = len(match.group(1))
                states.append(True)
            else:
                states.append(False)
            continue
        states.append(True)
        if (
            match
            and match.group(1)[0] == open_char
            and len(match.group(1)) >= open_len
            and not match.group(2).strip()
        ):
            open_char = ""
            open_len = 0
    return states, bool(open_char)


def fence_states(lines):
    """Map each line to True when it belongs to a fenced code block."""
    return _fence_scan(lines)[0]


def boundary_indices(lines):
    """Indices of section-boundary headings (H1/H2), ignoring code fences."""
    states = fence_states(lines)
    return [
        i
        for i, line in enumerate(lines)
        if not states[i] and _BOUNDARY_RE.match(line)
    ]


def heading_occurrences(lines, heading):
    """Indices of non-fenced lines that equal HEADING (trailing space aside)."""
    states = fence_states(lines)
    return [
        i
        for i, line in enumerate(lines)
        if not states[i] and line.rstrip() == heading
    ]


def _split_lines(text):
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # drop the empty tail produced by a trailing newline
    return lines


def _strip_trailing_blank(lines):
    while lines and not lines[-1].strip():
        lines.pop()


def validate_block(block_lines):
    """Return the block's heading line after checking the merge invariants."""
    if not block_lines or not _H2_RE.match(block_lines[0]):
        raise MergeError("block must start with an H2 heading line ('## ...')")
    if boundary_indices(block_lines) != [0]:
        raise MergeError(
            "block must contain exactly one H1/H2-level heading outside "
            "code fences: its own first line"
        )
    if _fence_scan(block_lines)[1]:
        # An unclosed fence in the block would swallow whatever follows the
        # merged section in the target — refuse instead of corrupting.
        raise MergeError("block ends inside an unclosed code fence")
    return block_lines[0].rstrip()


def _reject_setext_in_span(lines, start, end):
    """Refuse to replace across a possible setext heading.

    A ``===`` or ``---`` run directly under a non-blank line is (or may be)
    a real heading that ``_BOUNDARY_RE`` cannot see. Replacing across it
    would delete a section boundary without any error, so stop and report.
    """
    states = fence_states(lines)
    for index in range(start + 1, end):
        if states[index] or not _SETEXT_RE.match(lines[index]):
            continue
        if index - 1 <= start or states[index - 1]:
            continue
        previous = lines[index - 1]
        if not previous.strip() or _BOUNDARY_RE.match(previous):
            continue
        raise MergeError(
            "possible setext heading ('===' or '---' underline) inside the "
            "section at line %d; convert it to an ATX heading or use fixed "
            "markers before merging" % (index + 1)
        )


def merge(document_text, block_text):
    """Merge BLOCK into DOCUMENT (both LF-normalized text).

    Returns ``(new_text, action)`` where action is 'replaced', 'appended',
    or 'unchanged'. new_text always ends with exactly one newline.
    """
    doc_lines = _split_lines(document_text)
    block_lines = _split_lines(block_text)
    _strip_trailing_blank(block_lines)
    heading = validate_block(block_lines)
    block_lines[0] = heading  # heading is written without trailing spaces

    if _fence_scan(doc_lines)[1]:
        # CommonMark runs an unclosed fence to EOF, so the section would
        # extend to EOF and a replace would rewrite the whole visually
        # swallowed tail. Malformed input: stop and report instead.
        raise MergeError(
            "target ends inside an unclosed code fence; close it before merging"
        )

    occurrences = heading_occurrences(doc_lines, heading)
    if len(occurrences) > 1:
        raise MergeError(
            "target contains %d copies of '%s' outside code fences; "
            "deduplicate it before merging" % (len(occurrences), heading)
        )

    if occurrences:
        start = occurrences[0]
        end = len(doc_lines)
        for index in boundary_indices(doc_lines):
            if index > start:
                end = index
                break
        _reject_setext_in_span(doc_lines, start, end)
        new_span = list(block_lines)
        if end < len(doc_lines):
            # Exactly one blank line between this section and the next one,
            # so a second run reproduces the same bytes.
            new_span.append("")
        new_lines = doc_lines[:start] + new_span + doc_lines[end:]
        action = "replaced"
    else:
        new_lines = list(doc_lines)
        _strip_trailing_blank(new_lines)
        if new_lines:
            new_lines.append("")  # one blank line before the appended section
        new_lines.extend(block_lines)
        action = "appended"

    new_text = "\n".join(new_lines) + "\n"
    if new_text == document_text:
        action = "unchanged"
    return new_text, action


def _decode(raw, what):
    text = raw[len(_BOM):].decode("utf-8") if raw.startswith(_BOM) else raw.decode("utf-8")
    if re.search(r"\r(?!\n)", text):
        # A lone CR is invisible to LF/CRLF handling and would break
        # apply-twice-diff-zero (the second run would still change bytes).
        raise MergeError(
            "%s contains CR line endings that are not part of CRLF; "
            "convert the file to LF or CRLF before merging" % what
        )
    return text


def _inspect_target(target):
    """Return TARGET's ``lstat`` result, refusing link ambiguity."""

    try:
        target_stat = target.lstat()
    except FileNotFoundError:
        return None

    # Replacing a symlink atomically changes the link itself, while the old
    # direct write followed it. Refuse instead of silently changing which
    # filesystem object the command updates.
    if stat.S_ISLNK(target_stat.st_mode):
        raise MergeError("target is a symbolic link; refusing atomic replacement")

    # FIFOs, sockets, devices, and directories can block or perform side
    # effects when read. The command only rewrites ordinary regular files.
    if not stat.S_ISREG(target_stat.st_mode):
        raise MergeError("target is not a regular file; refusing atomic replacement")

    # An atomic rename necessarily breaks a hard-link set. Updating the shared
    # inode in place would preserve the links but reintroduce partial writes, so
    # require the caller to choose an ordinary single-link target explicitly.
    if target_stat.st_nlink > 1:
        raise MergeError("target has multiple hard links; refusing atomic replacement")

    return target_stat


def _open_atomic_temporary(target):
    """Create an exclusive same-directory temporary file for TARGET."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)

    # Same-directory creation keeps the final os.replace on one filesystem.
    # O_EXCL prevents a pre-created link from redirecting the temporary write.
    for _attempt in range(32):
        temporary = target.parent / (
            ".%s.%s.tmp" % (target.name, secrets.token_hex(12))
        )
        try:
            descriptor = os.open(temporary, flags, 0o666)
        except FileExistsError:
            continue
        return descriptor, temporary
    raise OSError("could not allocate an exclusive temporary file")


def _copy_posix_metadata(target, descriptor, target_stat):
    """Copy bounded metadata that an inode-replacing commit would lose."""

    if hasattr(os, "fchown"):
        os.fchown(descriptor, target_stat.st_uid, target_stat.st_gid)
    os.fchmod(descriptor, stat.S_IMODE(target_stat.st_mode))

    # Linux and several Unix variants expose ACL/security labels through
    # extended attributes. Preserve a bounded set or abort before commit;
    # silently dropping metadata is less safe than leaving the target intact.
    if not all(
        hasattr(os, name)
        for name in ("listxattr", "getxattr", "setxattr")
    ):
        return
    names = os.listxattr(target, follow_symlinks=False)
    if len(names) > _MAX_XATTR_COUNT:
        raise MergeError("target has too many extended attributes to preserve")

    total_bytes = 0
    for name in names:
        value = os.getxattr(target, name, follow_symlinks=False)
        total_bytes += len(os.fsencode(name)) + len(value)
        if total_bytes > _MAX_XATTR_BYTES:
            raise MergeError("target extended attributes exceed the preservation limit")
        os.setxattr(descriptor, name, value)


def _stat_fingerprint(value):
    """Return fields that expose replacement or mutation of one target."""

    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _open_windows_regular_read_descriptor(target, missing_ok):
    """Open TARGET without traversing a Windows reparse point."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = (
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    get_file_type = kernel32.GetFileType
    get_file_type.argtypes = (wintypes.HANDLE,)
    get_file_type.restype = wintypes.DWORD
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    )
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    generic_read = 0x80000000
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    file_flag_backup_semantics = 0x02000000
    invalid_handle = ctypes.c_void_p(-1).value

    handle = create_file(
        os.path.abspath(os.fspath(target)),
        generic_read,
        share_read_write_delete,
        None,
        open_existing,
        file_flag_open_reparse_point | file_flag_backup_semantics,
        None,
    )
    if handle == invalid_handle:
        error_code = ctypes.get_last_error()
        if missing_ok and error_code in (2, 3):
            return None
        _raise_windows_api_error(error_code, target)

    descriptor = None
    try:
        information = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            _raise_windows_api_error(ctypes.get_last_error(), target)

        file_type_disk = 0x0001
        file_attribute_directory = 0x0010
        file_attribute_reparse_point = 0x0400
        if (
            get_file_type(handle) != file_type_disk
            or information.dwFileAttributes & file_attribute_directory
        ):
            raise MergeError(
                "target is not a regular file; refusing atomic replacement"
            )
        if information.dwFileAttributes & file_attribute_reparse_point:
            raise MergeError(
                "target is a reparse point; refusing atomic replacement"
            )
        if information.nNumberOfLinks > 1:
            raise MergeError(
                "target has multiple hard links; refusing atomic replacement"
            )

        # open_osfhandle transfers ownership of HANDLE to the Python fd.
        # From this point os.close/fdopen is the only valid closer.
        descriptor = msvcrt.open_osfhandle(
            handle,
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
        handle = None
        return descriptor
    finally:
        if handle is not None:
            close_handle(handle)


def _open_regular_read_descriptor(target, missing_ok=False):
    """Open TARGET for a bounded-type read without following a link."""

    if os.name == "nt":
        return _open_windows_regular_read_descriptor(target, missing_ok)

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    # A FIFO opened read-only can otherwise wait forever before fstat gets a
    # chance to reject it. O_NONBLOCK has no effect on regular-file reads.
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        return os.open(target, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.EMLINK):
            raise MergeError(
                "target is a symbolic link; refusing atomic replacement"
            ) from error
        raise


def _read_regular_file_snapshot(target, missing_ok=False):
    """Read one stable regular-file object and verify its path identity."""

    descriptor = _open_regular_read_descriptor(target, missing_ok=missing_ok)
    if descriptor is None:
        return None, b""

    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise MergeError(
                "target is not a regular file; refusing atomic replacement"
            )
        if before.st_nlink > 1:
            raise MergeError(
                "target has multiple hard links; refusing atomic replacement"
            )

        # OPEN_REPARSE_POINT/O_NOFOLLOW protects the open itself. The path
        # checks also detect a rename/swap after the handle was acquired.
        path_before = _inspect_target(target)
        if (
            path_before is None
            or not _same_file_identity(path_before, before)
        ):
            raise MergeError("target changed while being opened")

        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read()
            after = os.fstat(stream.fileno())
            path_after = _inspect_target(target)
            if _stat_fingerprint(after) != _stat_fingerprint(before):
                raise MergeError("target changed while being read")
            if (
                path_after is None
                or not _same_file_identity(path_after, after)
            ):
                raise MergeError("target path changed while being read")
        return after, raw
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _assert_target_unchanged(target, target_stat, original_bytes):
    """Fail before commit when another writer changed TARGET."""

    current_before, current_bytes = _read_regular_file_snapshot(
        target,
        missing_ok=True,
    )
    if target_stat is None:
        if current_before is not None:
            raise MergeError("target appeared during merge; refusing to overwrite it")
        return
    if current_before is None:
        raise MergeError("target disappeared during merge")
    if _stat_fingerprint(current_before) != _stat_fingerprint(target_stat):
        raise MergeError("target metadata changed during merge")

    if current_bytes != original_bytes:
        raise MergeError("target content changed during merge")


def _move_file_windows_no_replace_raw(source, destination):
    """Return zero or the Win32 error from a no-replace durable move."""

    import ctypes
    from ctypes import wintypes

    move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    )
    move_file.restype = wintypes.BOOL
    movefile_write_through = 0x00000008
    if move_file(
        os.path.abspath(os.fspath(source)),
        os.path.abspath(os.fspath(destination)),
        movefile_write_through,
    ):
        return 0
    return ctypes.get_last_error()


def _move_new_windows_file(target, temporary):
    """Atomically install a missing Windows target without replacement."""

    error_code = _move_file_windows_no_replace_raw(temporary, target)
    if error_code:
        import ctypes

        raise OSError(
            error_code,
            ctypes.FormatError(error_code),
            os.path.abspath(os.fspath(target)),
        )


def _replace_file_windows_raw(target, temporary, backup):
    """Return zero or the Win32 error from ``ReplaceFileW``."""

    import ctypes
    from ctypes import wintypes

    replace_file = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    replace_file.restype = wintypes.BOOL
    if replace_file(
        os.path.abspath(os.fspath(target)),
        os.path.abspath(os.fspath(temporary)),
        os.path.abspath(os.fspath(backup)),
        0,
        None,
        None,
    ):
        return 0
    return ctypes.get_last_error()


def _open_windows_backup_placeholder(target):
    """Reserve one unpredictable same-directory ReplaceFileW backup name."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    for _attempt in range(32):
        backup = target.parent / (
            ".%s.%s.replace-backup" % (target.name, secrets.token_hex(12))
        )
        try:
            descriptor = os.open(backup, flags, 0o600)
        except FileExistsError:
            continue
        placeholder_stat = os.fstat(descriptor)
        try:
            os.fsync(descriptor)
        except Exception as setup_error:
            # 予約名を作った後の失敗では、閉じられたことと同じ file
            # identity のままであることを確認できた場合だけ片付ける。
            try:
                os.close(descriptor)
            except Exception:
                raise _windows_commit_state_error(
                    "Windows recovery backup setup failed while closing its placeholder",
                    committed=False,
                    recovered=True,
                    paths=(backup,),
                ) from setup_error
            try:
                _unlink_owned_path(backup, placeholder_stat)
            except Exception:
                raise _windows_commit_state_error(
                    "Windows recovery backup setup failed and its placeholder cleanup was unsafe",
                    committed=False,
                    recovered=True,
                    paths=(backup,),
                ) from setup_error
            raise
        try:
            os.close(descriptor)
        except Exception as close_error:
            # close 失敗時は descriptor が閉じたと推測せず、回復用の
            # placeholder を残して利用者へ明示する。
            raise _windows_commit_state_error(
                "Windows recovery backup placeholder could not be closed safely",
                committed=False,
                recovered=True,
                paths=(backup,),
            ) from close_error
        return backup, placeholder_stat
    raise OSError("could not allocate an exclusive recovery backup")


def _same_file_identity(left, right):
    """Compare stable filesystem identity without following path aliases."""

    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _path_matches_expected(path, expected_stat, expected_bytes):
    """Return whether PATH is the expected file object and exact bytes."""

    try:
        current, current_bytes = _read_regular_file_snapshot(
            path,
            missing_ok=True,
        )
        return (
            current is not None
            and _same_file_identity(current, expected_stat)
            and current_bytes == expected_bytes
        )
    except (MergeError, OSError):
        return False


def _unlink_owned_path(path, expected_stat):
    """Delete PATH only while it still names the object this call created."""

    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if not _same_file_identity(current, expected_stat):
        raise MergeError("recovery artifact identity changed; refusing cleanup")
    path.unlink()


def _existing_artifacts(paths):
    """Return paths that still exist without following dangling links."""

    existing = []
    for path in paths:
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        existing.append(path)
    return existing


def _windows_commit_state_error(message, committed, recovered, paths):
    """Create a structured partial-commit error and retain every artifact."""

    artifacts = _existing_artifacts(paths)
    suffix = ""
    if artifacts:
        suffix = "; retained artifacts: " + ", ".join(str(path) for path in artifacts)
    return AtomicCommitError(
        message + suffix,
        committed=committed,
        recovered=recovered,
        artifacts=artifacts,
    )


def _raise_windows_api_error(error_code, target):
    """Raise one native-style error after a safe rollback/cleanup."""

    import ctypes

    message = (
        ctypes.FormatError(error_code)
        if hasattr(ctypes, "FormatError")
        else "Windows error %d" % error_code
    )
    raise OSError(
        error_code,
        message,
        os.path.abspath(os.fspath(target)),
    )


def _commit_existing_windows(target, temporary, target_stat, original_bytes):
    """Own TEMPORARY and recover every documented ReplaceFileW partial state."""

    try:
        temporary_stat, replacement_bytes = _read_regular_file_snapshot(
            temporary,
        )
        if temporary_stat is None:
            raise MergeError("Windows replacement temporary disappeared")
    except Exception as temporary_error:
        raise _windows_commit_state_error(
            "Windows replacement temporary could not be verified before commit",
            committed=False,
            recovered=True,
            paths=(temporary,),
        ) from temporary_error

    backup = None
    placeholder_stat = None
    try:
        backup, placeholder_stat = _open_windows_backup_placeholder(target)
    except Exception as setup_error:
        try:
            _unlink_owned_path(temporary, temporary_stat)
        except Exception:
            setup_artifacts = (
                setup_error.artifacts
                if isinstance(setup_error, AtomicCommitError)
                else ()
            )
            raise _windows_commit_state_error(
                "Windows commit setup failed and temporary cleanup was unsafe",
                committed=False,
                recovered=True,
                paths=tuple(setup_artifacts) + (temporary,),
            ) from setup_error
        raise

    native_failure = None
    try:
        error_code = _replace_file_windows_raw(target, temporary, backup)
    except Exception as error:
        # A mock or loader failure is reconciled exactly like an unknown
        # Win32 result; ownership has already moved into this helper.
        native_failure = error
        error_code = None

    if error_code == 0:
        # Success should move the replacement object to TARGET and the old
        # target object to BACKUP. Verify both transitions before deleting the
        # only recovery copy.
        if not _path_matches_expected(
            target,
            temporary_stat,
            replacement_bytes,
        ):
            raise _windows_commit_state_error(
                "Windows replacement reported success but the committed "
                "target could not be verified",
                committed=True,
                recovered=False,
                paths=(backup, temporary),
            )
        if not _path_matches_expected(backup, target_stat, original_bytes):
            raise _windows_commit_state_error(
                "Windows replacement committed but its recovery backup "
                "could not be verified",
                committed=True,
                recovered=False,
                paths=(backup,),
            )
        try:
            _unlink_owned_path(backup, target_stat)
        except Exception:
            raise _windows_commit_state_error(
                "Windows replacement committed but its recovery backup "
                "could not be removed",
                committed=True,
                recovered=False,
                paths=(backup,),
            )
        return

    original_at_target = _path_matches_expected(
        target,
        target_stat,
        original_bytes,
    )
    original_at_backup = _path_matches_expected(
        backup,
        target_stat,
        original_bytes,
    )

    if original_at_backup:
        # ERROR_UNABLE_TO_MOVE_REPLACEMENT_2 (1177) documents this state.
        # Restore with no replace; a concurrently created target wins and both
        # owned recovery files are retained for explicit manual resolution.
        try:
            target.lstat()
        except FileNotFoundError:
            move_error = _move_file_windows_no_replace_raw(backup, target)
            if move_error:
                raise _windows_commit_state_error(
                    "Windows replacement failed and the original backup "
                    "could not be restored without replacement",
                    committed=False,
                    recovered=False,
                    paths=(backup, temporary),
                )
            original_at_target = _path_matches_expected(
                target,
                target_stat,
                original_bytes,
            )
            if not original_at_target:
                raise _windows_commit_state_error(
                    "Windows replacement rollback completed but the restored "
                    "target could not be verified",
                    committed=False,
                    recovered=False,
                    paths=(target, backup, temporary),
                )
        else:
            raise _windows_commit_state_error(
                "Windows replacement failed after another target appeared; "
                "original backup was retained",
                committed=False,
                recovered=False,
                paths=(backup, temporary),
            )

    if original_at_target:
        # 1175/1176 and ordinary failures retain the original names when a
        # backup was supplied. Delete only objects whose identity is ours.
        try:
            _unlink_owned_path(temporary, temporary_stat)
            if backup is not None:
                _unlink_owned_path(backup, placeholder_stat)
        except Exception:
            raise _windows_commit_state_error(
                "Windows replacement failed; original was recovered but "
                "artifact cleanup was unsafe",
                committed=False,
                recovered=True,
                paths=(backup, temporary),
            )
        if native_failure is not None:
            raise native_failure
        _raise_windows_api_error(error_code, target)

    raise _windows_commit_state_error(
        "Windows replacement failed and the original target state is ambiguous",
        committed=False,
        recovered=False,
        paths=(backup, temporary),
    )


def _commit_temporary(target, temporary, target_stat, original_bytes):
    """Install TEMPORARY while preserving platform security metadata."""

    if target_stat is None:
        if os.name == "nt":
            _move_new_windows_file(target, temporary)
        else:
            # link(2) is an atomic no-replace commit on one filesystem.
            # Removing the private temporary name leaves one ordinary link.
            os.link(temporary, target, follow_symlinks=False)
            temporary.unlink()
        return
    if os.name != "nt":
        os.replace(temporary, target)
        return

    # ReplaceFileW preserves ACLs, attributes, and named streams. Supplying a
    # private backup makes documented 1176/1177 partial failures recoverable.
    _commit_existing_windows(
        target,
        temporary,
        target_stat,
        original_bytes,
    )


def _atomic_write(target, data, target_stat, original_bytes):
    """Write DATA completely, then atomically replace TARGET."""

    descriptor = None
    temporary = None
    try:
        descriptor, temporary = _open_atomic_temporary(target)
        stream = os.fdopen(descriptor, "wb")
        descriptor = None
        with stream:
            stream.write(data)
            stream.flush()
            if target_stat is not None and os.name != "nt":
                _copy_posix_metadata(target, stream.fileno(), target_stat)
            os.fsync(stream.fileno())

        # Recheck both identity/version metadata and bytes after the temporary
        # file is durable. Existing-path replace APIs are not compare-and-swap:
        # this detects changes visible before the check, while callers needing
        # a strict lost-update guarantee must serialize every writer.
        _assert_target_unchanged(target, target_stat, original_bytes)
        if os.name == "nt" and target_stat is not None:
            # ReplaceFileW can partially move names even when it reports an
            # error. Transfer ownership before the call so outer cleanup never
            # destroys a recovery file whose state only the helper understands.
            commit_temporary = temporary
            temporary = None
            _commit_temporary(
                target,
                commit_temporary,
                target_stat,
                original_bytes,
            )
        else:
            _commit_temporary(
                target,
                temporary,
                target_stat,
                original_bytes,
            )
            temporary = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def merge_file(target, block, write=True):
    """Merge BLOCK file into TARGET file. Returns ``(changed, action)``.

    Action is 'replaced', 'appended', 'normalized' (content already
    canonical, only mixed line endings or a missing final newline were
    normalized), or 'unchanged'. The target's line-ending style and UTF-8
    BOM are preserved so that a second run is byte-identical. A file that
    mixes CRLF and LF is treated as CRLF (any CRLF present selects CRLF)
    and is stable from the second run on. Writes use a flushed same-directory
    temporary file and one atomic replace. Windows ACLs/attributes and bounded
    POSIX metadata are preserved. Symbolic-link, Windows reparse-point,
    non-regular, and multi-hard-link targets are refused before reading because
    atomic replacement would change their semantics. The pre-commit conflict
    recheck is best-effort rather than compare-and-swap; serialize writers
    externally for strict lost-update prevention.
    """
    target_stat, raw = _read_regular_file_snapshot(target, missing_ok=True)
    has_bom = raw.startswith(_BOM)
    text = _decode(raw, "target")
    eol = "\r\n" if "\r\n" in text else "\n"
    normalized = text.replace("\r\n", "\n")

    block_text = _decode(block.read_bytes(), "block").replace("\r\n", "\n")

    merged, action = merge(normalized, block_text)
    out = merged if eol == "\n" else merged.replace("\n", eol)
    out_bytes = (_BOM if has_bom else b"") + out.encode("utf-8")
    changed = out_bytes != raw
    if not changed:
        action = "unchanged"
    else:
        if action == "unchanged":
            action = "normalized"
        if write:
            _atomic_write(target, out_bytes, target_stat, raw)
    return changed, action


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Idempotently replace-or-append one Markdown '##' section."
    )
    parser.add_argument("target", type=Path, help="Markdown file to update")
    parser.add_argument(
        "block",
        type=Path,
        help="file whose first line is the '## ...' heading of the section",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether a merge would change TARGET; write nothing",
    )
    args = parser.parse_args(argv)

    try:
        changed, action = merge_file(args.target, args.block, write=not args.check)
    except (MergeError, OSError, UnicodeDecodeError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2

    if args.check:
        if changed:
            verb = {
                "replaced": "would-replace",
                "appended": "would-append",
                "normalized": "would-normalize",
            }.get(action, "would-change")
            print("%s: %s" % (verb, args.target))
            return 1
        print("up-to-date: %s" % args.target)
        return 0

    print("%s: %s" % (action, args.target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
