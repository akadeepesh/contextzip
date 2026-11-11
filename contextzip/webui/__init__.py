"""
webui — Local-only browser UI for setting up .contextzip/config.json.

Entry point: `launch_config_ui()` in server.py, invoked by
`contextzip config --ui`. Everything here runs a plain
stdlib HTTP server bound to 127.0.0.1 — no new dependency, and no data
about the project ever leaves the machine.
"""
