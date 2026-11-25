"""LIFE-972 (Jim, Nov 24 2026, 11:35 PM CT): the Recruiter Outreach card is redrawn by a background worker, never inside a Done or Dismiss click.

The Close service saves the record, answers at once and pokes this worker's socket (lifeos/platform/close_redraw.py). The worker (a socket-activated unit with its own copy of the Close Notion credential) redraws the card with the hourly writer
(outreach_card, its scope guard and read-back). One redraw at a time: pokes that arrive while one runs wait in the socket and are answered by ONE next redraw, however many there were. It exits after `IDLE` seconds
without a poke and is started again by the next one. Prints fixed words only (`redraw ok|fail`), never a name, id or token."""
import contextlib
import io
import json
import os
import socket
import sys

SETTINGS = "/etc/lifeos-close/redraw.json"                       # {"hiring_table": <block id>, "close_base": <https origin>}: its own file, so a regenerated policy.json never loses them
POLICY = "/etc/lifeos-close/policy.json"
IDLE = 20.0


def redraw(token, recruiters, hiring_table, close_base):
    """The card, rewritten from the Recruiters rows now, with the writer the hourly stage uses. -> True only when that writer's own read-back verified the table."""
    try:
        from lifeos.platform.notion_client import Client
        from lifeos.recruiters import outreach_card as card
        env = {"HIRING_TABLE_BLOCK_ID": hiring_table, "RECRUITER_OUTREACH": "on", "RECRUITERS_STAGE_OUTCOME": "skipped", "V7_CLOSE_BASE_URL": close_base}
        notion = Client({"NOTION_API_TOKEN": token, "NOTION_RECRUITERS_DATA_SOURCE_ID": recruiters}, source_name="NOTION_RECRUITERS_DATA_SOURCE_ID")
        writer = Client({"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})
        with contextlib.redirect_stdout(io.StringIO()):
            return bool(card.run(0, True, environ=env, notion=notion, client=writer).get("verified"))
    except Exception:                                                                # noqa: BLE001 - the next poke or the hourly run redraws; nothing is reported but the fixed word
        return False


def serve(listener, run, idle=IDLE):
    """Take one poke, take every other poke already waiting, then run ONE redraw; repeat. Returns when `idle` seconds pass with no poke."""
    while True:
        listener.settimeout(idle)
        try:
            conn, _ = listener.accept()
        except socket.timeout:
            return 0
        conn.close()
        listener.setblocking(False)
        try:
            while True:
                listener.accept()[0].close()
        except (BlockingIOError, InterruptedError):
            pass
        run()


def main(environ=os.environ, listener=None, run=None):
    if listener is None:
        listener = socket.socket(fileno=3)                       # systemd socket activation hands the one listening socket over as file descriptor 3
    if run is None:
        with open(os.path.join(environ.get("CREDENTIALS_DIRECTORY", ""), "notion"), "rb") as f:
            token = f.read().strip().decode()
        with open(POLICY) as f:
            recruiters = json.load(f).get("recruiters") or ""
        with open(SETTINGS) as f:
            settings = json.load(f)

        def run():
            print("redraw", "ok" if redraw(token, recruiters, settings["hiring_table"], settings["close_base"]) else "fail", flush=True)
    return serve(listener, run)


if __name__ == "__main__":
    sys.exit(main())
