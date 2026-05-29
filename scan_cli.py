"""
Scan an After Effects project's render queue from the command line.

Requires a local After Effects install (AfterFX.com + ExtendScript).
aerender.exe cannot read the render queue — only AfterFX can.

Example:
  python scan_cli.py "D:\\projects\\scene.aep"
  python scan_cli.py scene.aep -o queue.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from scan_queue import scan_project


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Scan an .aep render queue via AfterFX + ExtendScript."
    )
    parser.add_argument("aep", help="Path to the .aep project file")
    parser.add_argument(
        "-o",
        "--output",
        help="Write JSON here (default: print to stdout only)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Max seconds to wait for AfterFX (default: 600)",
    )
    args = parser.parse_args(argv)

    aep = os.path.normpath(os.path.abspath(args.aep))
    if not os.path.isfile(aep):
        print(f"Error: AEP not found: {aep}", file=sys.stderr)
        return 1

    def log(msg):
        print(msg, flush=True)

    try:
        data = scan_project(aep, log_callback=log, timeout=args.timeout)
    except Exception as exc:
        print(f"Scan failed: {exc}", file=sys.stderr)
        return 1

    if args.output:
        out_path = os.path.normpath(os.path.abspath(args.output))
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        log(f"Wrote {len(data.get('items', []))} item(s) to {out_path}")
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
