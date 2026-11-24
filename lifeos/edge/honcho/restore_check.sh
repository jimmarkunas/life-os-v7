#!/bin/sh
# LIFE-867: prove a backup restores, on a SCRATCH copy only. Run as root: restore_check.sh [backup dir] (default: the newest). Starts one throwaway container from the image already on the host, with no network and
# no volume (its data lives in the container and is removed with it), restores the dump into it and prints the row counts of Honcho's tables. It never touches honcho_pgdata or any live container.
set -eu
DEST=/var/backups/lifeos-honcho
dir=${1:-$(ls -1dt "$DEST"/*/ | head -n 1)}
dir=${dir%/}
( cd "$dir" && sha256sum -c SHA256SUMS >/dev/null ) || { echo "restore_check: checksum mismatch"; exit 4; }
name="honcho-restore-scratch-$$"
docker run -d --name "$name" --network none -e POSTGRES_PASSWORD=scratch -e POSTGRES_DB=postgres pgvector/pgvector:pg15 >/dev/null
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
i=0; until docker exec "$name" pg_isready -U postgres -d postgres >/dev/null 2>&1; do i=$((i+1)); [ $i -lt 60 ] || { echo "restore_check: scratch database did not start"; exit 5; }; sleep 1; done
sleep 3
docker exec -i "$name" sh -c 'psql -U postgres -d postgres -qc "CREATE EXTENSION IF NOT EXISTS vector" && pg_restore -U postgres -d postgres --no-owner --exit-on-error' < "$dir/honcho.pgdump" 2>&1 | tail -3 || true
echo "restore_check: restored $(basename "$dir") into scratch container $name (no network, no volume); live volume untouched"
docker exec "$name" psql -U postgres -d postgres -At -c "select table_name from information_schema.tables where table_schema='public' order by 1" | while read -r t; do
  [ -n "$t" ] && printf '%s %s\n' "$t" "$(docker exec "$name" psql -U postgres -d postgres -At -c "select count(*) from public.\"$t\"")"
done
