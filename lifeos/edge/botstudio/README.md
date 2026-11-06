# Bot Studio hosting (LIFE-327)

Private, tailnet-only hosting for the LIFE OS Bot Studio (built from the private repo `jimmarkunas/bot-studio`; the studio carries third-party marks, so its files are never committed here).

    sudo bash install.sh dry    --src DIST   # shows the plan, writes nothing
    sudo bash install.sh install --src DIST  # copies DIST, starts the service, publishes on the tailnet, reads back
    sudo bash install.sh status
    sudo bash install.sh remove

- Serves static files from `/opt/lifeos-botstudio/dist` by python3 `http.server` bound to 127.0.0.1:8788 (systemd `DynamicUser`, read-only filesystem, loopback-only network).
- Published with `tailscale serve --bg --https=8443` (tailnet only). `funnel` is never used. Port 443 (OpenClaw), 8787, 9119, 18789 are never touched.
- Source is refused if it lacks `index.html`, has symlinks, odd file names, disallowed types, or exceeds 5 MB.
- Previous copy is kept as `dist.prev`.
