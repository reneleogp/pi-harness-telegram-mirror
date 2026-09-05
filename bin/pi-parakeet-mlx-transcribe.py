#!/usr/bin/env python3
"""Pi's local macOS Parakeet MLX adapter.

Accepts exactly one audio path and prints only the transcript on stdout.
Parakeet's progress output is discarded on success and bounded diagnostics are
sent to stderr on failure. The output directory is private temporary storage
and is removed by TemporaryDirectory on every normal or handled signal exit.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import NoReturn

MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
STOP_TIMEOUT = 2.0


def fail(message: str) -> "NoReturn":
    print(f"Pi Parakeet transcription failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe one audio file locally with Parakeet MLX.")
    parser.add_argument("audio", type=Path)
    args = parser.parse_args()
    audio = args.audio
    if not audio.is_file():
        fail("the audio file is missing")
    if shutil.which("ffmpeg") is None:
        fail("ffmpeg is not installed or is not on PATH")
    executable = shutil.which("parakeet-mlx")
    if executable is None:
        fail("parakeet-mlx is not installed or is not on PATH")

    child: subprocess.Popen[bytes] | None = None
    with tempfile.TemporaryDirectory(prefix="pi-parakeet-") as output:
        os.chmod(output, 0o700)
        target = Path(output) / "transcript.txt"
        command = [
            executable,
            "transcribe",
            str(audio),
            "--model",
            MODEL,
            "--output-format",
            "txt",
            "--output-dir",
            output,
            "--output-template",
            "transcript",
        ]
        try:
            child = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )

            def stop(_signum: int, _frame: object) -> None:
                if child is None or child.poll() is not None:
                    return
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except OSError:
                    child.terminate()
                try:
                    child.wait(timeout=STOP_TIMEOUT)
                    return
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except OSError:
                    child.kill()
                try:
                    child.wait(timeout=STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    pass

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            diagnostics = bytearray()

            def drain() -> None:
                while True:
                    chunk = child.stderr.read(65536)
                    if not chunk:
                        return
                    if len(diagnostics) < 4096:
                        diagnostics.extend(chunk[:4096 - len(diagnostics)])

            reader = threading.Thread(target=drain)
            reader.start()
            child.wait()
            reader.join()
        except (OSError, ValueError) as exc:
            fail(f"could not start parakeet-mlx: {exc}")
        if child.returncode != 0:
            detail = bytes(diagnostics).decode("utf-8", "replace").strip()[:240]
            fail(f"parakeet-mlx exited with status {child.returncode}"
                 + (f": {detail}" if detail else ""))
        try:
            transcript = target.read_text(encoding="utf-8").strip()
        except OSError as exc:
            fail(f"parakeet-mlx did not produce transcript text: {exc}")
        if not transcript:
            fail("parakeet-mlx produced empty transcript text")
        print(transcript)
    return 0


if __name__ == "__main__":
    main()
