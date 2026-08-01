#!/usr/bin/env python3
"""Characterize merge_section.py memory amplification with synthetic data.

The public mode launches one fresh worker process per case and repetition.  A
worker generates bounded Markdown in an exclusive temporary directory, runs
the real atomic merge, and emits one small JSON record.  The low-overhead
default records process peak RSS; optional ``tracemalloc`` records traced
Python allocations after fixture creation and module import.  Neither metric
is an application memory guarantee or a CI threshold.
"""

import argparse
import gc
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path


SCHEMA_VERSION = 1
PROCESS_PEAK_METRIC = "process-peak-rss"
TRACEMALLOC_METRIC = "python-tracemalloc"
DEFAULT_METRIC = PROCESS_PEAK_METRIC
METRIC_CHOICES = (PROCESS_PEAK_METRIC, TRACEMALLOC_METRIC)
DEFAULT_TARGET_BYTES = 8 * 1024 * 1024
MIN_TARGET_BYTES = 16 * 1024
MAX_REPETITIONS = 10
DEFAULT_REPETITIONS = 3
DEFAULT_TIMEOUT_SECONDS = 180
MAX_TIMEOUT_SECONDS = 600
MAX_WORKER_OUTPUT_BYTES = 64 * 1024
WRITE_CHUNK_BYTES = 64 * 1024

BLOCK_NEW_LF = b"## Managed\n\nnew-body\n"
BLOCK_OLD_LF = b"## Managed\n\nold-body\n"

CASE_SPECS = (
    ("lf-short-lines-append", "appended"),
    ("lf-short-lines-replace", "replaced"),
    ("crlf-short-lines-append", "appended"),
    ("crlf-short-lines-replace", "replaced"),
    ("mixed-eol-normalize", "normalized"),
)
CASE_IDS = tuple(case_id for case_id, _action in CASE_SPECS)
EXPECTED_ACTIONS = dict(CASE_SPECS)


class MeasurementError(Exception):
    """A fixed, path-free measurement contract failure."""


def _bytes_with_eol(lf_bytes, eol):
    """Return a tiny canonical block with the requested final line ending."""
    return lf_bytes if eol == b"\n" else lf_bytes.replace(b"\n", eol)


def _short_line_shape(final_bytes, final_eol):
    """Describe short ``x`` lines whose final encoding is exactly N bytes."""
    unit_bytes = 1 + len(final_eol)
    line_count, remainder = divmod(final_bytes, unit_bytes)
    if line_count < 2:
        raise MeasurementError("target byte budget is too small for the fixture")

    # 端数は最後の通常行へ足し、Markdown構造を持つ記号は生成しない。
    last_text_bytes = 1 + remainder if remainder else 1
    return line_count, last_text_bytes, bool(remainder)


def _write_repeated(handle, unit, count):
    """Write COUNT copies without building the multi-megabyte fixture in RAM."""
    if count <= 0:
        return
    per_chunk = max(1, WRITE_CHUNK_BYTES // len(unit))
    chunk = unit * per_chunk
    while count >= per_chunk:
        handle.write(chunk)
        count -= per_chunk
    if count:
        handle.write(unit * count)


def _write_short_lines(handle, final_bytes, final_eol, mixed_actual=False):
    """Write lines sized by FINAL_EOL, optionally storing all but one as LF.

    ``mixed_actual`` models a target whose first line is CRLF and whose
    remaining lines are LF.  The returned raw byte count can therefore be
    smaller than ``final_bytes`` even though normalization expands back to the
    exact requested final size.
    """
    line_count, last_text_bytes, has_remainder = _short_line_shape(
        final_bytes,
        final_eol,
    )

    if not mixed_actual:
        regular_count = line_count - (1 if has_remainder else 0)
        _write_repeated(handle, b"x" + final_eol, regular_count)
        if has_remainder:
            handle.write((b"x" * last_text_bytes) + final_eol)
        return line_count, final_bytes

    if final_eol != b"\r\n":
        raise MeasurementError("mixed-EOL fixture requires CRLF final encoding")

    # CRLFを1行だけ残すことでmerge_fileのCRLF選択を確実にし、残りをLFにする。
    handle.write(b"x\r\n")
    regular_after_first = line_count - 1 - (1 if has_remainder else 0)
    _write_repeated(handle, b"x\n", regular_after_first)
    if has_remainder:
        handle.write((b"x" * last_text_bytes) + b"\n")
    raw_bytes = final_bytes - (line_count - 1)
    return line_count, raw_bytes


def _open_exclusive(path):
    """Create fixture files without overwriting a caller-provided path."""
    return path.open("xb")


def _prepare_case(case_id, work_dir, target_bytes):
    """Stream one synthetic target and return its expected merge contract."""
    target = work_dir / "target.md"
    block = work_dir / "section.md"
    with _open_exclusive(block) as handle:
        handle.write(BLOCK_NEW_LF)

    if case_id.startswith("lf-"):
        final_eol = b"\n"
    else:
        final_eol = b"\r\n"

    block_final = _bytes_with_eol(BLOCK_NEW_LF, final_eol)
    old_block_final = _bytes_with_eol(BLOCK_OLD_LF, final_eol)
    expected_action = EXPECTED_ACTIONS[case_id]

    with _open_exclusive(target) as handle:
        if case_id.endswith("append"):
            prefix_final_bytes = target_bytes - len(final_eol) - len(block_final)
            line_count, raw_prefix_bytes = _write_short_lines(
                handle,
                prefix_final_bytes,
                final_eol,
            )
            target_bytes_before = raw_prefix_bytes
        elif case_id.endswith("replace"):
            prefix_final_bytes = target_bytes - len(old_block_final)
            line_count, raw_prefix_bytes = _write_short_lines(
                handle,
                prefix_final_bytes,
                final_eol,
            )
            handle.write(old_block_final)
            target_bytes_before = raw_prefix_bytes + len(old_block_final)
            line_count += old_block_final.count(final_eol)
        elif case_id == "mixed-eol-normalize":
            prefix_final_bytes = target_bytes - len(block_final)
            line_count, raw_prefix_bytes = _write_short_lines(
                handle,
                prefix_final_bytes,
                b"\r\n",
                mixed_actual=True,
            )
            # managed section自体はLFで保存し、同一contentのEOL正規化だけを測る。
            handle.write(BLOCK_NEW_LF)
            target_bytes_before = raw_prefix_bytes + len(BLOCK_NEW_LF)
            line_count += BLOCK_NEW_LF.count(b"\n")
        else:  # pragma: no cover - argparse and parent validation close this path
            raise MeasurementError("unknown measurement case")

    return {
        "target": target,
        "block": block,
        "target_bytes_before": target_bytes_before,
        "target_line_count": line_count,
        "block_bytes": len(BLOCK_NEW_LF),
        "expected_action": expected_action,
        "expected_final_bytes": target_bytes,
    }


def _load_merge_module():
    """Import only inside workers so the parent never retains parser state."""
    scripts_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(scripts_dir))
    import merge_section  # pylint: disable=import-outside-toplevel

    return merge_section


def _process_peak_bytes():
    """Return the process lifetime peak resident set using stdlib OS APIs."""
    if os.name == "nt":
        # WindowsのPeakWorkingSetSizeはprocess lifetime値で、pollingを要しない。
        import ctypes  # pylint: disable=import-outside-toplevel
        from ctypes import wintypes  # pylint: disable=import-outside-toplevel

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_fault_count", wintypes.DWORD),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        if not psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(),
            ctypes.byref(counters),
            counters.cb,
        ):
            raise MeasurementError("process peak memory query failed")
        return int(counters.peak_working_set_size)

    # Python documents ru_maxrss as bytes on macOS and KiB on Linux.
    import resource  # pylint: disable=import-outside-toplevel

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _measure_worker(case_id, repetition, work_dir, target_bytes, metric):
    """Run the production merge once and return a path-free sample record."""
    merge_section = _load_merge_module()
    fixture = _prepare_case(case_id, work_dir, target_bytes)

    # fixture生成とmodule importをtracemallocから除外し、merge allocationへ絞る。
    gc.collect()
    if metric == TRACEMALLOC_METRIC:
        tracemalloc.start(1)
    started_ns = time.perf_counter_ns()
    changed, actual_action = merge_section.merge_file(
        fixture["target"],
        fixture["block"],
        write=True,
    )
    elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
    if metric == TRACEMALLOC_METRIC:
        current_bytes, traced_peak_bytes = tracemalloc.get_traced_memory()
        tracer_overhead_bytes = tracemalloc.get_tracemalloc_memory()
        tracemalloc.stop()
    else:
        current_bytes = None
        traced_peak_bytes = None
        tracer_overhead_bytes = None
    process_peak_bytes = _process_peak_bytes()
    peak_bytes = (
        traced_peak_bytes
        if metric == TRACEMALLOC_METRIC
        else process_peak_bytes
    )

    final_bytes = fixture["target"].stat().st_size
    remaining = sorted(path.name for path in work_dir.iterdir())
    temporary_artifact_count = len(
        [name for name in remaining if name not in {"target.md", "section.md"}]
    )

    # 数値を記録する前にaction・byte・cleanup契約を固定条件として検証する。
    if not changed or actual_action != fixture["expected_action"]:
        raise MeasurementError("worker action contract failed")
    if fixture["target_bytes_before"] > DEFAULT_TARGET_BYTES:
        raise MeasurementError("worker target byte contract failed")
    if fixture["block_bytes"] > 2 * 1024 * 1024:
        raise MeasurementError("worker block byte contract failed")
    if final_bytes != fixture["expected_final_bytes"]:
        raise MeasurementError("worker final byte contract failed")
    if peak_bytes <= 0:
        raise MeasurementError("worker peak memory contract failed")
    if current_bytes is not None and current_bytes > peak_bytes:
        raise MeasurementError("worker tracemalloc contract failed")
    if temporary_artifact_count:
        raise MeasurementError("worker temporary cleanup contract failed")

    input_bytes = fixture["target_bytes_before"] + fixture["block_bytes"]
    return {
        "case_id": case_id,
        "repetition": repetition,
        "target_bytes_before": fixture["target_bytes_before"],
        "block_bytes": fixture["block_bytes"],
        "final_bytes": final_bytes,
        "target_line_count": fixture["target_line_count"],
        "expected_action": fixture["expected_action"],
        "actual_action": actual_action,
        "changed": changed,
        "metric": metric,
        "elapsed_ms": round(elapsed_ms, 3),
        "current_bytes": current_bytes,
        "peak_bytes": peak_bytes,
        "process_peak_bytes": process_peak_bytes,
        "tracer_overhead_bytes": tracer_overhead_bytes,
        "peak_to_input_ratio": round(peak_bytes / input_bytes, 6),
        "temporary_artifact_count": temporary_artifact_count,
    }


def _worker_entry(args):
    """Emit one bounded JSON record; never reflect temporary paths or content."""
    try:
        sample = _measure_worker(
            args.case_id,
            args.repetition,
            Path(args.work_dir),
            args.target_bytes,
            args.metric,
        )
    except Exception as exc:  # worker boundary converts details to a type only
        error = {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "case_id": args.case_id,
            "error_type": type(exc).__name__,
        }
        print(json.dumps(error, ensure_ascii=False, sort_keys=True))
        return 1

    record = {"schema_version": SCHEMA_VERSION, "ok": True, "sample": sample}
    print(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


def _validate_worker_record(record, case_id, repetition, target_bytes, metric):
    """Reject malformed or cross-case worker output before aggregation."""
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        raise MeasurementError("worker schema contract failed")
    if record.get("ok") is not True or not isinstance(record.get("sample"), dict):
        raise MeasurementError("worker failed")
    sample = record["sample"]
    required = {
        "case_id",
        "repetition",
        "target_bytes_before",
        "block_bytes",
        "final_bytes",
        "target_line_count",
        "expected_action",
        "actual_action",
        "changed",
        "metric",
        "elapsed_ms",
        "current_bytes",
        "peak_bytes",
        "process_peak_bytes",
        "tracer_overhead_bytes",
        "peak_to_input_ratio",
        "temporary_artifact_count",
    }
    if set(sample) != required:
        raise MeasurementError("worker sample schema contract failed")
    if sample["case_id"] != case_id or sample["repetition"] != repetition:
        raise MeasurementError("worker identity contract failed")
    if sample["metric"] != metric:
        raise MeasurementError("worker metric contract failed")
    if sample["final_bytes"] != target_bytes:
        raise MeasurementError("worker output size contract failed")

    # boolはintのsubclassなので、JSON数値契約ではexact typeを要求する。
    integer_fields = (
        "repetition",
        "target_bytes_before",
        "block_bytes",
        "final_bytes",
        "target_line_count",
        "peak_bytes",
        "process_peak_bytes",
        "temporary_artifact_count",
    )
    if any(type(sample[name]) is not int for name in integer_fields):
        raise MeasurementError("worker sample type contract failed")
    if not isinstance(sample["elapsed_ms"], (int, float)) or isinstance(
        sample["elapsed_ms"],
        bool,
    ):
        raise MeasurementError("worker elapsed type contract failed")
    if not isinstance(sample["peak_to_input_ratio"], (int, float)) or isinstance(
        sample["peak_to_input_ratio"],
        bool,
    ):
        raise MeasurementError("worker ratio type contract failed")

    expected_action = EXPECTED_ACTIONS[case_id]
    if (
        sample["expected_action"] != expected_action
        or sample["actual_action"] != expected_action
        or sample["changed"] is not True
    ):
        raise MeasurementError("worker action contract failed")
    if not 0 < sample["target_bytes_before"] <= target_bytes:
        raise MeasurementError("worker target byte contract failed")
    if not 0 < sample["block_bytes"] <= 2 * 1024 * 1024:
        raise MeasurementError("worker block byte contract failed")
    if sample["target_line_count"] <= 0:
        raise MeasurementError("worker line-count contract failed")
    if sample["peak_bytes"] <= 0 or sample["process_peak_bytes"] <= 0:
        raise MeasurementError("worker peak memory contract failed")
    if (
        not math.isfinite(sample["elapsed_ms"])
        or not math.isfinite(sample["peak_to_input_ratio"])
        or sample["elapsed_ms"] < 0
        or sample["peak_to_input_ratio"] <= 0
    ):
        raise MeasurementError("worker numeric contract failed")
    expected_ratio = round(
        sample["peak_bytes"]
        / (sample["target_bytes_before"] + sample["block_bytes"]),
        6,
    )
    if not math.isclose(
        sample["peak_to_input_ratio"],
        expected_ratio,
        rel_tol=0,
        abs_tol=1e-9,
    ):
        raise MeasurementError("worker ratio contract failed")
    if sample["temporary_artifact_count"] != 0:
        raise MeasurementError("worker temporary cleanup contract failed")

    if metric == PROCESS_PEAK_METRIC:
        if (
            sample["current_bytes"] is not None
            or sample["tracer_overhead_bytes"] is not None
            or sample["peak_bytes"] != sample["process_peak_bytes"]
        ):
            raise MeasurementError("worker process metric contract failed")
    else:
        if (
            type(sample["current_bytes"]) is not int
            or type(sample["tracer_overhead_bytes"]) is not int
            or not 0 <= sample["current_bytes"] <= sample["peak_bytes"]
            or sample["tracer_overhead_bytes"] <= 0
        ):
            raise MeasurementError("worker tracemalloc contract failed")
    return sample


def _read_pipe_bounded(stream, process, sink, overflow, failures):
    """Drain one worker pipe while retaining at most limit + 1 bytes."""
    captured = bytearray()
    try:
        while True:
            # 上限+1を観測した時点でchildを止め、capture_outputの無制限保持を避ける。
            read_size = min(
                4096,
                MAX_WORKER_OUTPUT_BYTES + 1 - len(captured),
            )
            chunk = stream.read(read_size)
            if not chunk:
                break
            captured.extend(chunk)
            if len(captured) > MAX_WORKER_OUTPUT_BYTES:
                overflow.set()
                try:
                    process.kill()
                except OSError:
                    pass
                break
    except Exception:
        failures.append(True)
        try:
            process.kill()
        except OSError:
            pass
    finally:
        sink.append(bytes(captured))
        try:
            stream.close()
        except OSError:
            pass


def _run_bounded_process(command, timeout_seconds):
    """Run a child with concurrent hard-capped stdout/stderr readers."""
    process = None
    started_threads = []
    try:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise MeasurementError("worker process could not start") from exc

        if process.stdout is None or process.stderr is None:  # pragma: no cover
            raise MeasurementError("worker pipe setup failed")

        overflow = threading.Event()
        failures = []
        stdout_parts = []
        stderr_parts = []
        threads = [
            threading.Thread(
                target=_read_pipe_bounded,
                args=(
                    process.stdout,
                    process,
                    stdout_parts,
                    overflow,
                    failures,
                ),
                daemon=True,
            ),
            threading.Thread(
                target=_read_pipe_bounded,
                args=(
                    process.stderr,
                    process,
                    stderr_parts,
                    overflow,
                    failures,
                ),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
            started_threads.append(thread)

        timed_out = False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise MeasurementError("worker termination timed out") from exc

        # kill / normal exitでpipeが閉じた後だけ、短い固定時間でreaderを回収する。
        for thread in started_threads:
            thread.join(timeout=5)
        if any(thread.is_alive() for thread in started_threads):
            process.kill()
            raise MeasurementError("worker output collection failed")
        if failures:
            raise MeasurementError("worker output collection failed")
        if overflow.is_set():
            raise MeasurementError("worker output exceeded its byte limit")
        if timed_out:
            raise MeasurementError("worker timed out")

        return (
            process.returncode,
            stdout_parts[0] if stdout_parts else b"",
            stderr_parts[0] if stderr_parts else b"",
        )
    finally:
        # Thread.start / KeyboardInterrupt等を含む全経路でchildとpipeを回収する。
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass
        for thread in started_threads:
            thread.join(timeout=5)


def _run_worker(case_id, repetition, target_bytes, timeout_seconds, metric):
    """Launch one fresh bounded worker and discard its private directory."""
    with tempfile.TemporaryDirectory(prefix="markdown-peak-memory-") as directory:
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--case-id",
            case_id,
            "--repetition",
            str(repetition),
            "--work-dir",
            directory,
            "--target-bytes",
            str(target_bytes),
            "--metric",
            metric,
        ]
        returncode, stdout_bytes, stderr_bytes = _run_bounded_process(
            command,
            timeout_seconds,
        )
        if returncode != 0 or stderr_bytes:
            raise MeasurementError("worker process failed")
        try:
            stdout_text = stdout_bytes.decode("utf-8")
            record = json.loads(stdout_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MeasurementError("worker emitted invalid JSON") from exc
        return _validate_worker_record(
            record,
            case_id,
            repetition,
            target_bytes,
            metric,
        )


def _number_summary(samples, key):
    values = [sample[key] for sample in samples]
    return {
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def _build_report(target_bytes, repetitions, timeout_seconds, case_ids, metric):
    """Aggregate descriptive statistics without turning values into gates."""
    cases = []
    for case_id in case_ids:
        samples = [
            _run_worker(
                case_id,
                repetition,
                target_bytes,
                timeout_seconds,
                metric,
            )
            for repetition in range(1, repetitions + 1)
        ]
        cases.append(
            {
                "case_id": case_id,
                "summary": {
                    "sample_count": len(samples),
                    "peak_bytes": _number_summary(
                        samples,
                        "peak_bytes",
                    ),
                    "elapsed_ms": _number_summary(samples, "elapsed_ms"),
                    "peak_to_input_ratio": _number_summary(
                        samples,
                        "peak_to_input_ratio",
                    ),
                },
                "samples": samples,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "measurement": metric,
        "configuration": {
            "target_bytes": target_bytes,
            "repetitions": repetitions,
            "timeout_seconds": timeout_seconds,
            "case_ids": list(case_ids),
            "metric": metric,
        },
        "environment": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "cases": cases,
        "limitations": [
            (
                "process peak RSS covers the whole worker lifetime, including "
                "fixture generation and module import"
                if metric == PROCESS_PEAK_METRIC
                else "tracemalloc observes traced Python allocations, not process RSS"
            ),
            "peak values and ratios are descriptive and are not pass/fail thresholds",
            "each sample runs in a fresh bounded subprocess with synthetic data",
        ],
    }


def _bounded_int(name, minimum, maximum):
    def parse(value):
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("%s must be an integer" % name) from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                "%s must be between %d and %d" % (name, minimum, maximum)
            )
        return parsed

    return parse


def _build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Measure synthetic merge cases in fresh subprocesses with process "
            "peak RSS by default or tracemalloc as an explicit diagnostic. "
            "Values are descriptive, not application memory guarantees."
        )
    )
    parser.add_argument(
        "--target-bytes",
        type=_bounded_int("target-bytes", MIN_TARGET_BYTES, DEFAULT_TARGET_BYTES),
        default=DEFAULT_TARGET_BYTES,
        help="final target size per case (default: 8 MiB)",
    )
    parser.add_argument(
        "--repetitions",
        type=_bounded_int("repetitions", 1, MAX_REPETITIONS),
        default=DEFAULT_REPETITIONS,
        help="fresh worker processes per case (default: 3)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=_bounded_int("timeout-seconds", 1, MAX_TIMEOUT_SECONDS),
        default=DEFAULT_TIMEOUT_SECONDS,
        help="timeout for each worker process (default: 180)",
    )
    parser.add_argument(
        "--metric",
        choices=METRIC_CHOICES,
        default=DEFAULT_METRIC,
        help=(
            "peak metric: process-peak-rss (default, low overhead) or "
            "python-tracemalloc (diagnostic, high overhead)"
        ),
    )
    parser.add_argument(
        "--case",
        dest="case_ids",
        action="append",
        choices=CASE_IDS,
        help="run only this case; may be repeated",
    )

    # Worker options are deliberately hidden. Exclusive file creation keeps a
    # direct worker invocation from overwriting pre-existing target files.
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--case-id", choices=CASE_IDS, help=argparse.SUPPRESS)
    parser.add_argument("--repetition", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--work-dir", help=argparse.SUPPRESS)
    return parser


def _emit_fixed_error(message):
    """Best-effort fixed stderr without allowing a second traceback."""
    try:
        sys.stderr.write(message + "\n")
    except Exception:
        pass


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.worker:
        if args.case_id is None or args.repetition is None or args.work_dir is None:
            parser.error("worker arguments are incomplete")
        return _worker_entry(args)

    case_ids = tuple(dict.fromkeys(args.case_ids or CASE_IDS))
    try:
        report = _build_report(
            args.target_bytes,
            args.repetitions,
            args.timeout_seconds,
            case_ids,
            args.metric,
        )
        payload = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        sys.stdout.write(payload + "\n")
    except MeasurementError as exc:
        _emit_fixed_error("error: %s" % exc)
        return 2
    except Exception:
        # 予期しないOS/temp/decode失敗もpathやfixture内容をtracebackへ反射しない。
        _emit_fixed_error("error: measurement failed")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
