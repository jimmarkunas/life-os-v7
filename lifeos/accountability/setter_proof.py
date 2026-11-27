"""Manual proof that the integration can edit the V2_OFFICIAL row: one same-value update (off over off), then a read-back.

Never scheduled. A dry run (the default) only reads the row and reports; `--live` sends the single request. The
expected revision is the one the run was approved against."""

import argparse
import os
import sys

from lifeos.accountability import common, official_setter


def main(argv=None, environ=None, client=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--revision", type=int, required=True, help="the Revision the row must hold before the request")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    client = client or common.notion_writer(environ)
    try:
        out = official_setter.prove_same_value(
            client, environ.get("V7_RUNTIME_CONFIG_SOURCE_ID", ""), args.live, args.revision
        )
    except official_setter.WriteError as error:
        print(f"setter proof: STOP {error.code}")
        return 1
    print("setter proof: " + " ".join(f"{k}={v}" for k, v in out.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
