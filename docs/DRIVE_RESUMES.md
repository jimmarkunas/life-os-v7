# Google Drive résumés (Jim, Oct 24 2026; LIFE-88)
Build résumé saves each .docx in Google Drive (never converted) and links it in Notion; "I applied with this résumé" locks the file read-only and sets the
Ledger's Submitted Résumé. The Close service on the Edge uses its own credential `gdrive`, scope `drive.file` only (files LIFE OS creates), granted to
the existing **LIFE OS Gmail Web** OAuth client in Google Cloud project **life-os-gmail-508407**. Its client ID and secret are in Bitwarden Secrets
Manager, project LIFE OS EDGE / OPENCLAW: `openclaw/gmail/clientId` and `openclaw/gmail/clientSecret` (they sit side by side; check which one you copy).
The client lists `http://127.0.0.1:8765` as an authorized redirect URI. The Edge's separate read-only Drive grant (`openclaw/drive/refreshToken`) is untouched.
1. On the Mac (no SSH tunnel), fetch `lifeos/platform/google_consent.py` from main and run:
   `python3 google_consent.py --port 8765 | ssh opc@lifeos-edge-01 "sudo bash /opt/lifeos-close/bin/install-close.sh credential gdrive"`
2. Paste the client ID, then the secret; open the printed Google address in the browser and Allow. It ends with `encrypted credential gdrive stored`.
   A failed or aborted sign-in stores nothing and keeps any working credential. Port 8765 must be free (close any old `ssh -L 8765` first).
3. The first build creates a **Résumés** folder at the top of My Drive; move it into My Drive / LIFE OS once (it is found by its tag).
Undo: `install-close.sh credential gdrive-off` (builds go back to the Notion upload).
