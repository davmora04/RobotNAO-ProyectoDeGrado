#!/usr/bin/env python3
"""Send a sample pedagogical event to the robot HTTP bridge."""

from __future__ import annotations

import argparse
import json
import pathlib
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--event",
        default=str(pathlib.Path(__file__).resolve().parents[1] / "config" / "sample_feedback_event.json"),
    )
    args = parser.parse_args()

    event_path = pathlib.Path(args.event)
    payload = event_path.read_text(encoding="utf-8")
    request = urllib.request.Request(
        f"http://{args.host}:{args.port}/robot/feedback",
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()

