# Vendored driver for the Buzz MCP release

`pymysql/` is PyMySQL 1.1.1, pure Python, unmodified, taken from the wheel pinned in `requirements.lock` (sha256 `4de15da4c61dc132f4fb9ab763063e693d521a80fd0e87943b9a453dd4c19d6c`), with its MIT license. The gateway runs on the system Python with no packages installed, so the release carries this copy; `lifeos/platform/graph_tools.py` puts the release's `vendor/` folder on the import path only when it first reads the graph checkpoint.

To change it: change the pin in `requirements.txt` and `requirements.lock`, unpack the new wheel here, and rebuild the manifest (`python3 tools/buzz_release.py manifest`).
