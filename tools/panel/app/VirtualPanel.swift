// Virtual Panel: the browser front panel (tools/panel) as a macOS app.
// One window, one WKWebView. The app spawns panel_server.py itself, shows a
// placeholder until GET /status answers, then loads the page (the page shows
// the firmware's own boot / project-load phase). Single file, no Xcode
// project: tools/panel/app/build.sh compiles it with swiftc, the way
// tools/hw/rec.swift is built.
//
//   bash tools/panel/app/build.sh && open "out/Virtual Panel.app"
//   VIRTUAL_PANEL_PORT=8571 "out/Virtual Panel.app/Contents/MacOS/VirtualPanel"
//
// Port 8563 unless VIRTUAL_PANEL_PORT says otherwise. A server that already
// answers on the port is attached to and left alone on quit; a server the
// app spawned is terminated on quit, window close, SIGTERM/SIGINT/SIGHUP.
// Everything the server prints, and the app's own lines, go to
// out/panel_app.log.
import Cocoa
import WebKit

// MARK: - repo root, log

/// The repo the app drives. build.sh bakes the absolute path into
/// Contents/Resources/repo_root; if that is missing or stale (bundle or repo
/// moved) walk up from the bundle looking for pyproject.toml.
func findRepoRoot() -> URL? {
    let fm = FileManager.default
    if let f = Bundle.main.url(forResource: "repo_root", withExtension: nil),
       let s = try? String(contentsOf: f, encoding: .utf8) {
        let p = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if fm.fileExists(atPath: p + "/pyproject.toml") { return URL(fileURLWithPath: p) }
    }
    var dir = Bundle.main.bundleURL.deletingLastPathComponent()
    for _ in 0..<12 {
        if fm.fileExists(atPath: dir.appendingPathComponent("pyproject.toml").path) { return dir }
        let up = dir.deletingLastPathComponent()
        if up.path == dir.path { break }
        dir = up
    }
    return nil
}

/// out/panel_app.log: the server's stdout/stderr and the app's own lines in
/// one file. O_APPEND on both descriptors so the two writers interleave
/// whole lines instead of overwriting each other.
enum Log {
    static var fd: Int32 = -1
    static var path = ""
    static let stamp: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm:ss"; return f
    }()

    static func open(_ url: URL) {
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                 withIntermediateDirectories: true)
        path = url.path
        fd = Darwin.open(url.path, O_WRONLY | O_CREAT | O_APPEND, 0o644)
    }

    static func write(_ s: String) {
        let line = "\(stamp.string(from: Date())) app: \(s)\n"
        if fd >= 0 {
            line.withCString { _ = Darwin.write(fd, $0, strlen($0)) }
        } else {
            FileHandle.standardError.write(line.data(using: .utf8)!)
        }
    }

    /// Last `n` lines, for the failure placeholder.
    static func tail(_ n: Int) -> String {
        guard let s = try? String(contentsOfFile: path, encoding: .utf8) else { return "" }
        return s.split(separator: "\n", omittingEmptySubsequences: false).suffix(n).joined(separator: "\n")
    }
}

// MARK: - the server

/// panel_server.py on 127.0.0.1:<port> -- either the process this app
/// spawned or one that was already answering when the app started.
final class PanelServer {
    let repo: URL
    let port: Int
    private(set) var process: Process?     // the one we spawned, if any
    var attached = false                   // answered when last probed and not ours to stop
    var onExit: ((String) -> Void)?        // a spawned server died: "status N" / "signal N (SIGxxx)" (main thread)

    init(repo: URL, port: Int) { self.repo = repo; self.port = port }

    var url: URL { URL(string: "http://127.0.0.1:\(port)/")! }
    var statusURL: URL { url.appendingPathComponent("status") }
    var running: Bool { process?.isRunning ?? false }

    /// How a spawned server ended. terminationStatus is the exit code, or the
    /// signal number when a signal ended it (terminationReason says which):
    /// the SIGBUS panel_server.py takes when its bind fails while the
    /// emulator thread is already up came out as "status 10" before.
    static let signalNames: [Int32: String] = [
        SIGHUP: "SIGHUP", SIGINT: "SIGINT", SIGQUIT: "SIGQUIT", SIGILL: "SIGILL", SIGTRAP: "SIGTRAP",
        SIGABRT: "SIGABRT", SIGFPE: "SIGFPE", SIGKILL: "SIGKILL", SIGBUS: "SIGBUS", SIGSEGV: "SIGSEGV",
        SIGPIPE: "SIGPIPE", SIGALRM: "SIGALRM", SIGTERM: "SIGTERM",
    ]
    static func ending(of p: Process) -> String {
        let n = p.terminationStatus
        guard p.terminationReason == .uncaughtSignal else { return "status \(n)" }
        return "signal \(n) (\(signalNames[n] ?? "?"))"
    }

    /// The command line for a project directory (nil = the empty card, which
    /// boots to SET DATE/TIME). --set is the parent folder, --name the folder:
    /// out/_projects/otlive/OTLIVE/PROJECT -> --set OTLIVE --name PROJECT.
    func arguments(project: URL?) -> [String] {
        var a = [repo.appendingPathComponent("tools/panel/panel_server.py").path, "--port", String(port)]
        if let p = project {
            a += ["--project", p.path,
                  "--set", p.deletingLastPathComponent().lastPathComponent,
                  "--name", p.lastPathComponent]
        }
        return a
    }

    func commandLine(project: URL?) -> String {
        ([repo.appendingPathComponent(".venv/bin/python3").path] + arguments(project: project)).joined(separator: " ")
    }

    func spawn(project: URL?) throws {
        let p = Process()
        p.executableURL = repo.appendingPathComponent(".venv/bin/python3")   // the arm64 venv (EMAC-fixed Unicorn)
        p.arguments = arguments(project: project)
        p.currentDirectoryURL = repo
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:" + (env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
        env["PYTHONUNBUFFERED"] = "1"   // else stdout is block-buffered to the file: prints land minutes late
        p.environment = env
        // a bad descriptor makes NSTask raise; if the log could not be opened the server's output is dropped
        let log = Log.fd >= 0 ? FileHandle(fileDescriptor: Log.fd, closeOnDealloc: false) : FileHandle.nullDevice
        p.standardOutput = log
        p.standardError = log
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self = self, self.process === proc else { return }   // stop() already took it
                self.process = nil
                self.onExit?(Self.ending(of: proc))
            }
        }
        try p.run()
        process = p
        Log.write("spawned pid \(p.processIdentifier): \(commandLine(project: project))")
    }

    /// SIGTERM the spawned server and wait for it (SIGKILL after 3 s).
    /// Python has no SIGTERM handler, so the default action ends it at once
    /// even mid-Unicorn-burst. No-op for an attached server.
    func stop() {
        guard let p = process else { return }
        process = nil
        guard p.isRunning else { return }
        p.terminate()
        let deadline = Date().addingTimeInterval(3)
        while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL); p.waitUntilExit() }
        Log.write("server pid \(p.processIdentifier) stopped (\(Self.ending(of: p)))")
    }

    static let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: c)
    }()

    /// GET /status. ok = HTTP 200 carrying JSON. Completion on the main thread;
    /// a refused connection fails within milliseconds, so a 500 ms poll is cheap.
    func probe(timeout: TimeInterval, _ done: @escaping (Bool, [String: Any]?) -> Void) {
        let req = URLRequest(url: statusURL, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        Self.session.dataTask(with: req) { data, resp, _ in
            var st: [String: Any]? = nil
            if let d = data, (resp as? HTTPURLResponse)?.statusCode == 200 {
                st = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any]
            }
            DispatchQueue.main.async { done(st != nil, st) }
        }.resume()
    }
}

// MARK: - the app

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate {
    static let projectKey = "projectDir"   // UserDefaults: the Open Project... choice

    let repo: URL
    let server: PanelServer
    var window: NSWindow!
    var web: WKWebView!
    var poll: Timer?
    var probing = false
    var probeGen = 0                       // startServer probes older than this are dropped
    var panelShown = false                 // the web view is on the server's page
    var signalSources: [DispatchSourceSignal] = []

    init(repo: URL, port: Int) {
        self.repo = repo
        self.server = PanelServer(repo: repo, port: port)
        super.init()
    }

    func applicationDidFinishLaunching(_ n: Notification) {
        buildMenus()
        buildWindow()
        installSignalHandlers()
        server.onExit = { [weak self] how in
            guard let self = self else { return }
            Log.write("server exited: \(how)")
            self.stopPolling()
            self.panelShown = false
            self.showPlaceholder("the panel server exited: \(how)", failed: true)
        }
        startServer()
    }

    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool { true }
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ n: Notification) {
        stopPolling()
        server.stop()
        Log.write("quit")
    }

    /// SIGTERM/SIGINT/SIGHUP (kill, ctrl-C in the shell that launched it,
    /// terminal closed) go through NSApp.terminate so the server is stopped
    /// too. SIGKILL cannot be caught: that one orphans a spawned server
    /// (pkill -f panel_server.py). SIGUSR1 is File > Reload, for scripts
    /// (menus cannot be driven without the Accessibility grant).
    func installSignalHandlers() {
        let quit: () -> Void = { NSApp.terminate(nil) }
        let reload: () -> Void = { [weak self] in self?.reload(nil) }
        for (sig, act) in [(SIGTERM, quit), (SIGINT, quit), (SIGHUP, quit), (SIGUSR1, reload)] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler { act() }
            src.resume()
            signalSources.append(src)
        }
    }

    // MARK: window, menus

    func buildWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1440, height: 860)
        window = NSWindow(contentRect: rect,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Virtual Panel"
        window.minSize = NSSize(width: 960, height: 560)
        window.delegate = self
        // center() first: setFrameAutosaveName returns true whenever the name
        // was taken (false only if another window owns it), so gating center()
        // on it left a first launch at the bottom-left corner beside the Dock
        // (measured 11 Sep 2026: frame 51 0 1440 888). A saved frame, if
        // there is one, overrides the centered position.
        window.center()
        _ = window.setFrameAutosaveName("VirtualPanelWindow")
        web = WKWebView(frame: rect, configuration: WKWebViewConfiguration())
        web.autoresizingMask = [.width, .height]
        web.navigationDelegate = self
        web.underPageBackgroundColor = NSColor(srgbRed: 0.086, green: 0.086, blue: 0.090, alpha: 1)  // panel.html's body
        window.contentView = web
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func buildMenus() {
        let main = NSMenu()

        let appItem = NSMenuItem(); main.addItem(appItem)
        let app = NSMenu()
        app.addItem(withTitle: "About Virtual Panel",
                    action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "Quit Virtual Panel", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = app

        let fileItem = NSMenuItem(); main.addItem(fileItem)
        let file = NSMenu(title: "File")
        file.addItem(withTitle: "Open Project...", action: #selector(openProject(_:)), keyEquivalent: "o").target = self
        file.addItem(withTitle: "Reload", action: #selector(reload(_:)), keyEquivalent: "r").target = self
        fileItem.submenu = file

        // Edit: the panel's key-map drawer has text fields and EXPORT MAP uses the clipboard.
        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let winItem = NSMenuItem(); main.addItem(winItem)
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        win.addItem(.separator())
        win.addItem(withTitle: "Bring All to Front", action: #selector(NSApplication.arrangeInFront(_:)), keyEquivalent: "")
        winItem.submenu = win

        NSApp.mainMenu = main
        NSApp.windowsMenu = win
    }

    // MARK: server lifecycle

    /// The project to load: the remembered Open Project... choice, else the
    /// ot-tools fixture if it has been fetched (tools/panel/README.md), else
    /// none -- the empty card, which boots to SET DATE/TIME.
    func projectDir() -> URL? {
        let fm = FileManager.default
        if let p = UserDefaults.standard.string(forKey: Self.projectKey) {
            if fm.fileExists(atPath: p) { return URL(fileURLWithPath: p) }
            Log.write("remembered project is gone, ignoring: \(p)")
        }
        let d = repo.appendingPathComponent("out/_projects/otlive/OTLIVE/PROJECT")
        return fm.fileExists(atPath: d.path) ? d : nil
    }

    /// Attach if something already answers on the port, else spawn and poll.
    /// Also Reload's path when the server is not ours: a probe that fails
    /// then means it is gone and a fresh one is spawned, rather than waiting.
    func startServer() {
        stopPolling()
        probeGen += 1
        let gen = probeGen
        server.attached = false
        showPlaceholder("looking for a server on port \(server.port)")
        server.probe(timeout: 1.0) { [weak self] ok, status in
            guard let self = self, gen == self.probeGen else { return }   // superseded by a later Reload / Open Project
            if ok {
                self.server.attached = true
                Log.write("a server already answers on port \(self.server.port): attaching (it stays up on quit); status \(status ?? [:])")
                self.loadPanel()
                return
            }
            self.launch(project: self.projectDir())
        }
    }

    func launch(project: URL?) {
        let what = project.map { "project \($0.deletingLastPathComponent().lastPathComponent)/\($0.lastPathComponent)" } ?? "empty card"
        do {
            try server.spawn(project: project)
        } catch {
            Log.write("spawn failed: \(error)")
            showPlaceholder("could not start the panel server: \(error.localizedDescription) -- is .venv built (scripts/setup.sh)?", failed: true)
            return
        }
        showPlaceholder("starting the panel server on port \(server.port) (\(what))")
        startPolling()
    }

    /// Open Project...: a spawned server is replaced; an attached one is not
    /// ours to restart, so say what to run instead.
    func restartServer(project: URL?) {
        if server.attached {
            let a = NSAlert()
            a.messageText = "The server on port \(server.port) was not started by this app"
            a.informativeText = "Restart it yourself with the project, then choose File > Reload:\n\n"
                + server.commandLine(project: project)
            a.runModal()
            return
        }
        stopPolling()
        probeGen += 1          // drop a startServer probe still in flight
        server.stop()
        panelShown = false
        launch(project: project)
    }

    func startPolling() {
        stopPolling()
        poll = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in self?.pollOnce() }
    }

    func stopPolling() {
        poll?.invalidate()
        poll = nil
    }

    func pollOnce() {
        if probing { return }
        probing = true
        server.probe(timeout: 0.4) { [weak self] ok, status in
            guard let self = self else { return }
            self.probing = false
            guard ok, self.poll != nil else { return }
            self.stopPolling()
            Log.write("server answered /status: \(status ?? [:])")
            self.loadPanel()
        }
    }

    func loadPanel() {
        panelShown = true
        web.load(URLRequest(url: server.url, cachePolicy: .reloadIgnoringLocalCacheData))
    }

    // MARK: menu actions

    @objc func openProject(_ sender: Any?) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Load"
        panel.message = "A project folder saved on a unit (SET/PROJECT; the set's AUDIO folder beside it is staged too)"
        let projects = repo.appendingPathComponent("out/_projects")
        if FileManager.default.fileExists(atPath: projects.path) { panel.directoryURL = projects }
        panel.beginSheetModal(for: window) { [weak self] resp in
            guard let self = self, resp == .OK, let dir = panel.url else { return }
            UserDefaults.standard.set(dir.path, forKey: Self.projectKey)
            Log.write("project chosen (remembered): \(dir.path)")
            self.restartServer(project: dir)
        }
    }

    /// cmd-R (or SIGUSR1). Our own server: reload the page, or keep polling
    /// while it boots. Otherwise (attached, or ours has died) probe again:
    /// whatever answers now is attached to and its page loaded, and nothing
    /// answering means spawn -- so a dead attached server is replaced on the
    /// first Reload instead of being waited for forever (the old isUp check
    /// never cleared `attached`).
    @objc func reload(_ sender: Any?) {
        Log.write("reload")
        if server.running {
            if panelShown { web.reload() } else { startPolling() }
        } else {
            startServer()
        }
    }

    // MARK: web view

    func webView(_ w: WKWebView, didFinish nav: WKNavigation!) {
        if let u = w.url, u.scheme == "http" { Log.write("loaded \(u)") }
    }

    /// The server went away between /status and the page (or was killed):
    /// back to the placeholder and keep polling. Not spawning here on
    /// purpose: an attached server may be the user's own being restarted
    /// (the Open Project alert tells them to), and a second bind on the port
    /// would fail; Reload is the explicit "replace it". -999 is our own
    /// loadHTMLString cancelling an in-flight load, not a failure.
    func webView(_ w: WKWebView, didFailProvisionalNavigation nav: WKNavigation!, withError e: Error) {
        if (e as NSError).code == NSURLErrorCancelled { return }
        Log.write("navigation failed: \(e.localizedDescription)")
        panelShown = false
        showPlaceholder("the server stopped answering (\(e.localizedDescription)); waiting for it"
                        + (server.running ? "" : " -- File > Reload starts a fresh one"))
        startPolling()
    }

    // MARK: placeholder

    func showPlaceholder(_ phase: String, failed: Bool = false) {
        Log.write("phase: \(phase)")
        let esc = { (s: String) -> String in
            s.replacingOccurrences(of: "&", with: "&amp;")
             .replacingOccurrences(of: "<", with: "&lt;")
             .replacingOccurrences(of: ">", with: "&gt;")
        }
        let tail = failed ? "<pre>\(esc(Log.tail(14)))</pre>" : ""
        let html = """
        <!doctype html><meta charset="utf-8"><title>Virtual Panel</title>
        <style>
          body { margin:0; background:#161617; color:#d8d5cc; height:100vh; display:flex;
                 align-items:center; justify-content:center; font:14px/1.5 -apple-system, "Helvetica Neue", sans-serif; }
          #box { text-align:center; max-width:760px; padding:0 24px; }
          .lcd { width:256px; height:128px; margin:0 auto 26px; background:#c9cdc4; border:3px solid #0a0a0a;
                 border-radius:4px; box-shadow:inset 0 0 18px #0006; opacity:.45; }
          h1 { font-weight:500; font-size:22px; margin:0 0 8px; }
          #phase { color:#8a877e; }  #phase.fail { color:#e5533a; }
          .small { color:#5f5d56; font-size:11px; margin-top:18px; }
          pre { text-align:left; font:11px/1.4 Menlo, monospace; color:#8a877e; background:#0c0c0d;
                border:1px solid #2b2b2d; border-radius:6px; padding:10px 12px; overflow:auto; max-height:260px; }
        </style>
        <div id="box">
          <div class="lcd"></div>
          <h1>Booting the firmware...</h1>
          <div id="phase" class="\(failed ? "fail" : "")">\(esc(phase))</div>
          \(tail)
          <div class="small">port \(server.port) &middot; log: \(esc(Log.path))</div>
        </div>
        """
        web.loadHTMLString(html, baseURL: nil)
    }
}

// MARK: - main

let app = NSApplication.shared
let port = Int(ProcessInfo.processInfo.environment["VIRTUAL_PANEL_PORT"] ?? "") ?? 8563
guard let repo = findRepoRoot() else {
    let a = NSAlert()
    a.messageText = "Virtual Panel: repo not found"
    a.informativeText = "Neither Contents/Resources/repo_root nor any parent of \(Bundle.main.bundlePath) has a pyproject.toml. Rebuild with tools/panel/app/build.sh."
    a.runModal()
    exit(1)
}
// First launch from Finder / `open` with the repo under ~/Downloads (or another
// TCC-protected folder): this open() blocks until the "access files in your
// Downloads folder" dialog is answered (sampled 11 Sep 2026: main thread in
// __open for minutes, no window yet). A terminal-launched instance inherits
// the terminal's grant and never sees the dialog.
Log.open(repo.appendingPathComponent("out/panel_app.log"))
Log.write("start: repo \(repo.path), port \(port), bundle \(Bundle.main.bundlePath)")
let delegate = AppDelegate(repo: repo, port: port)
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
