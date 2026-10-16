"""Detect stale worker proofs using the installed ha CLI; never write proof records."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

WORKERS = ("codex", "agy", "opencode")


def _json_command(argv: list[str]) -> tuple[int, object]:
    # Bound a broken CLI and its descendants. systemd additionally owns the cgroup.
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=3600)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise
    return process.returncode, json.loads(output)


def _event(path: Path, rail: str, version: object, passed: bool) -> None:
    # One append write; version output cannot inject additional coordination lines.
    clean = " ".join(str(version or "unknown").split())
    line = f"PROVE {rail} {clean} {'passed' if passed else 'failed'}\n".encode()
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _settled(report: object, rail: str) -> tuple[bool, bool]:
    if not isinstance(report, dict) or report.get("schema") != 1:
        return False, True
    verdicts, modes = report.get("verdicts"), report.get("modes")
    if not isinstance(verdicts, list) or not isinstance(modes, dict):
        return False, True
    ok = modes.get(rail) in ("parallel", "writes serialised") and all(
        isinstance(v, dict)
        and v.get("rail") == rail
        and v.get("outcome") == "passed"
        and v.get("recorded") is True
        for v in verdicts
    )
    return ok, bool(verdicts) or not ok


def cycle(ha: str, events: Path, rails: list[str]) -> int:
    events.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(events.parent / "worker-proof.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            code, providers = _json_command([ha, "providers", "--json"])
            if code or not isinstance(providers, list):
                raise ValueError("invalid providers response")
            rows = {p["name"]: p for p in providers if isinstance(p, dict) and "name" in p}
            if any(rail not in rows for rail in rails):
                raise ValueError("providers response omits a requested worker")
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            print(f"worker discovery failed: {exc}", file=sys.stderr)
            for rail in rails:
                _event(events, rail, None, False)
            return 1
        failed = False
        for rail in rails:
            row = rows[rail]
            if row.get("available") is not True:
                continue
            ok, attempted = False, True
            try:
                code, report = _json_command([ha, "prove", rail, "--stale", "--json"])
                ok, attempted = _settled(report, rail)
                ok = code == 0 and ok
                attempted = attempted or code != 0
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                print(f"{rail} proof command failed: {exc}", file=sys.stderr)
            if attempted:
                _event(events, rail, row.get("version"), ok)
            failed = failed or not ok
        return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ha",
        default=str(Path(sys.argv[0]).resolve().with_name("ha")),
        help="installed ha executable (default: sibling of this entry point)",
    )
    parser.add_argument(
        "--events", type=Path, default=Path.home() / ".local/state/red-coordination/events.log"
    )
    parser.add_argument("--rails", nargs="+", choices=WORKERS, default=list(WORKERS))
    args = parser.parse_args()
    return cycle(args.ha, args.events, list(dict.fromkeys(args.rails)))


if __name__ == "__main__":
    raise SystemExit(main())
