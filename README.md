# codium-copilot

Run `python3 codium-copilot.py` from the repository directory,
outside VSCodium, with Python 3.7+ and the `requests` and `packaging` packages installed.

## Automatic API compatibility repair

The installer detects the proposals implemented by your installed VSCodium runtime
and checks the extension's declared requirements before choosing a version.
It reads the standalone proposals file when available, or the embedded table in
`workbench.desktop.main.js` on bundle-only installations such as Fedora packages.
After installing the original VSIX, it adds runtime-supported proposals to the
installed Copilot Chat manifest and its user-level `product.json` allowlist.
This handles undeclared requirements such as `environmentPower` and
`agentSessionsWorkspace` without maintaining a fixed list of proposal names.
Existing declarations, including version suffixes, are preserved.

The original installed manifest is saved beside it as
`package.json.copilot-backup`. The installer launches VSCodium with
`--enable-proposed-api GitHub.copilot-chat`.

This grants Copilot Chat access to **all detected runtime proposals**, not just
those it currently declares. It does not grant these permissions to other
extensions or implement APIs missing from VSCodium. Installation stops before
removing existing extensions if the runtime proposal list cannot be detected.

Rerun the installer after updating VSCodium or Copilot Chat: extension updates can
replace the repaired manifest, and newer VSCodium releases can add proposals.
This is an API-permission workaround, not a guarantee that every future extension
version will be compatible.