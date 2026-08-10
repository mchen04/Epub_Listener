"""Tiny dependency-free local TTS stand-in used by integration tests."""

from __future__ import annotations

import argparse
import math
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--text-file", type=Path)
    parser.add_argument("--voice", default="")
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--child-marker", type=Path)
    parser.add_argument("--noise", type=int, default=0)
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args()

    text = sys.stdin.read()
    if args.text_file and args.text_file.read_text(encoding="utf-8") != text:
        print("stdin and text file differ", file=sys.stderr)
        return 3
    if args.noise:
        print("N" * args.noise, file=sys.stderr)
    if args.exit_code:
        return args.exit_code
    if args.child_marker:
        child_code = (
            "import pathlib,time; time.sleep(0.2); "
            f"pathlib.Path({str(args.child_marker)!r}).write_text('leaked')"
        )
        subprocess.Popen(  # noqa: S603 - controlled executable and fixture-only code
            [sys.executable, "-c", child_code]
        )
    if args.sleep:
        time.sleep(args.sleep)

    sample_rate = 16_000
    duration = max(0.2, min(1.0, len(text) / 120))
    frames = round(sample_rate * duration)
    voice_offset = sum(args.voice.encode("utf-8")) % 80
    with wave.open(str(args.output), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        for index in range(frames):
            value = int(5_000 * math.sin(2 * math.pi * (440 + voice_offset) * index / sample_rate))
            output.writeframesraw(struct.pack("<h", value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
