"""Pages the phone when Oracle's boot-volume backups are at their limit (LIFE-808): at the cap, the next weekly backup of the Edge is refused.

Oracle allows 5 backups here (the `backup-count` limit). On Nov 16 2026 the weekly backup failed with "Quota exceeded" because 5 existed, and nobody was told for 7 days.
Input: {"used", "available"} in Oracle's `oracle1` answer (stats.mjs: the limits service's own count, read as the VM), read every tick by the Oracle Cloud card.
Output: pushes `boot_backups_full` (INFO: phone only, reminded every 24 h while it stays true) through the platform alerts gateway (D53). It fires when no slot is free, so it
warns before a backup is lost, not after. A reading that cannot be had is unknown and changes nothing. State: one private row. Counts and fixed codes only; it never raises."""
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
