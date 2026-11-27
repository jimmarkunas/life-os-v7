"""Pages the phone when Oracle's boot-volume backups are at their limit.

Input: {"used", "available"} in Oracle's `oracle1` answer. Output: pushes `boot_backups_full` (INFO: phone only,
reminded every 24 h while true) when no slot is free. An unobtainable reading changes nothing. Never raises."""
from lifeos.oraclecloud.backup_watch import make
from lifeos.platform import alerts

KEY = "boot_backups_full"
CODE = "ORACLE_BOOT_BACKUPS_FULL"
ACTION = "in the Oracle console: delete the oldest boot-volume backup you no longer need (a manual one first); the weekly backup needs one free slot (LIFE-808)"


def healthy(reading):
    """-> True | False | None (unknown). Broken: no free backup slot."""
    if reading is None:
        return None
    return reading["available"] > 0


def alert_for(reading):
    used = reading["used"]
    return alerts.Alert(KEY, alerts.INFO, f"Oracle boot-volume backups are full: {used} of {used + reading['available']} in use",
                        f"The next weekly backup will be refused until one is deleted ({CODE}).", ACTION)


decide, record = make("boot-backup-watch", "v7_boot_backups", "bootbackups", KEY, healthy, alert_for)
