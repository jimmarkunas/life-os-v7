# WhatsApp kill switch (LIFE-643 row 8)

Goal: cut LIFE OS off from WhatsApp in about one minute. Step 1 is the one that really revokes access; the rest stops the software on the host. Nothing here deletes anything.

## From your phone (do this first)
1. WhatsApp > Settings > Linked devices.
2. Tap each LIFE OS linked device (the dedicated Attention one, and the OpenClaw one if you linked it) > Log out. This ends the session on WhatsApp's side; the files on the host become useless.

## From a terminal (host)
3. Ask the agent for the pack `LIFE-643-row8` (it prints its sha8), then type the approval line `run LIFE-643 <sha8>`, then run the two lines the receipt gives you (`scp -r` the folder, then `ssh -t opc@lifeos-edge-01 'sudo /usr/local/sbin/lifeos-run LIFE-643 <sha8>'`).
4. The pack stops and disables the Attention socket and service (and the old ubuntu user unit), then moves the session folders into `/var/lib/lifeos-runner/quarantine/whatsapp-<time>` (root only, mode 700). It prints `ok` or `skipped` per step and never reads a session file. Running it twice is safe.
5. OpenClaw's WhatsApp channel is NOT touched by the pack (no fixed documented off command exists). Manual: stop the OpenClaw WhatsApp channel from OpenClaw's own settings, or unlink its device on the phone (step 2).

## Check
`RESULT ok`, and the READBACK lines show `lifeos_wa_attention_service=ok` (or `skipped` if already off) and `session_new_state=ok` (or `skipped`).

## Undo
Re-pair from scratch (a new QR scan) and re-run the row 1 setup. The quarantine folder can be deleted by hand once you are sure; the pack never does it.
