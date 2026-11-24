#!/bin/sh
# LIFE-867: a backup of the one Honcho database on Oracle's own disk. Run as root. Nothing is stopped and nothing live is written: a logical dump (pg_dump) of the running database, the settings file and the image digest,
# into /var/backups/lifeos-honcho/<UTC stamp>/ (0700 root). Keeps the 7 newest backups and refuses when the disk has under 3 GB free. Names and sizes only on stdout.
set -eu
DEST=/var/backups/lifeos-honcho
NEED_KB=3145728
free_kb=$(df -Pk "$DEST" 2>/dev/null | awk 'NR==2{print $4}' || true)
[ -d "$DEST" ] || { mkdir -p "$DEST"; chmod 700 "$DEST"; free_kb=$(df -Pk "$DEST" | awk 'NR==2{print $4}'); }
[ "${free_kb:-0}" -ge "$NEED_KB" ] || { echo "backup: refused, under 3 GB free"; exit 3; }
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dir="$DEST/$stamp"
mkdir -m 700 "$dir"
docker exec honcho-database-1 sh -c 'pg_dump -U postgres -d postgres --format=custom --no-owner' > "$dir/honcho.pgdump"
cp -p /opt/lifeos-honcho/honcho.env "$dir/honcho.env"
docker image inspect ghcr.io/plastic-labs/honcho:v3.6.1 --format '{{index .RepoDigests 0}}' > "$dir/image-digest.txt"
sha256sum "$dir/honcho.pgdump" > "$dir/SHA256SUMS"
chmod 600 "$dir"/*
echo "backup: $stamp pgdump_bytes=$(stat -c %s "$dir/honcho.pgdump") free_kb=$free_kb"
ls -1dt "$DEST"/*/ | tail -n +8 | while read -r old; do rm -rf -- "$old"; echo "backup: pruned $(basename "$old")"; done
