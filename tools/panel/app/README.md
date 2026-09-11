# Virtual Panel, the macOS app

The browser front panel (`tools/panel/`) in its own window instead of a
browser tab. One Cocoa window with a `WKWebView`; the app starts
`panel_server.py` itself, waits for `/status`, then shows the page. Single
Swift file, compiled with `swiftc` the way `tools/hw/rec.swift` is -- no
Xcode project.

```sh
bash tools/panel/app/build.sh          # -> out/Virtual Panel.app (ad-hoc signed)
open "out/Virtual Panel.app"
```

## What it does

- Spawns `<repo>/.venv/bin/python3 tools/panel/panel_server.py --port 8563
  [--project DIR --set SET --name NAME]` with `/opt/homebrew/bin` first on
  `PATH`, stdout/stderr appended to `out/panel_app.log` (the app's own lines
  are in the same file, prefixed `app:`).
- Shows "Booting the firmware..." with the app-side phase until `GET /status`
  answers (polled every 500 ms), then loads `http://127.0.0.1:8563/`; the
  page itself shows the firmware phase (booting, loading project, ready).
- If something already answers on the port when the app starts, it attaches
  to that server and leaves it running on quit.
- The spawned server is terminated on quit, on window close and on
  SIGTERM/SIGINT/SIGHUP to the app. Only `kill -9` of the app orphans it
  (`pkill -f panel_server.py`). `kill -USR1 <app pid>` is File > Reload,
  for scripts (the menu itself needs the Accessibility grant to drive).
- When the spawned server dies the placeholder says how, with the log tail:
  `status N` for an exit code, `signal N (SIGxxx)` when a signal ended it
  (a bind failure on a busy port ends in the interpreter's SIGBUS, measured
  11 Sep 2026, which used to read "status 10").
- `VIRTUAL_PANEL_PORT=8571` in the environment picks another port
  (`open --env VIRTUAL_PANEL_PORT=8571 "out/Virtual Panel.app"`, or run
  `out/Virtual Panel.app/Contents/MacOS/VirtualPanel` from a shell).
- The repo root is baked into `Contents/Resources/repo_root` by `build.sh`;
  if that path no longer has a `pyproject.toml` the app walks up from the
  bundle's location instead.
- First launch from Finder or `open`, with the repo under `~/Downloads` (or
  another folder macOS guards): the system asks whether Virtual Panel may
  access that folder, and the app waits on the dialog before its window
  appears (measured 11 Sep 2026: the main thread blocks in `open()` of
  `out/panel_app.log` until the dialog is answered). Allow it once. An
  instance started from a terminal inherits the terminal's grant and never
  sees the dialog.

## Menus

- **File > Open Project...** (cmd-O): a project folder saved on a unit;
  `--set` is its parent folder's name, `--name` the folder's own. The server
  is restarted with it and the choice is remembered (UserDefaults
  `projectDir` under `io.octabam.virtual-panel`) for the next launch. When
  nothing is remembered and `out/_projects/otlive/OTLIVE/PROJECT` exists
  (the ot-tools fixture, `tools/panel/README.md`), that is loaded; otherwise
  the empty card, which boots to SET DATE/TIME. An attached server cannot be
  restarted by the app: it shows the command line to run instead.
- **File > Reload** (cmd-R): reloads the page when the server is the app's
  own (or keeps polling while it boots). When the app is attached to someone
  else's server it probes again: still answering, its page is reloaded;
  gone, a fresh server is spawned on the first Reload. A page load that
  fails on its own (server killed under a shown page) only goes back to the
  placeholder and polls -- it does not spawn, so a server the user is
  restarting by hand (what the Open Project alert asks for) is not raced for
  the port.
- Window: Minimize, Zoom. Edit: the clipboard for the key-map drawer.

Window: 1440x860 to start, centered on the first launch, 960x560 minimum,
size and position remembered (`NSWindow Frame VirtualPanelWindow` in the
app's defaults; `defaults delete io.octabam.virtual-panel "NSWindow Frame
VirtualPanelWindow"` forgets it).

## Files

| file | is |
|---|---|
| `VirtualPanel.swift` | the whole app |
| `Info.plist` | bundle id `io.octabam.virtual-panel`, min macOS 13 |
| `build.sh` | swiftc -O (explicit arm64 target + SDK: a Rosetta shell otherwise loses both), bundle assembly, `repo_root`, icon, `codesign -s -` |
| `make_icon.py` | draws the icon PNG (stdlib); `build.sh` runs sips + iconutil on it |
