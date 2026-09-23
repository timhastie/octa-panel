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
//
// Samples (12 Sep 2026): File > Add Samples to Card... (cmd-shift-A), files
// dropped on the window or the Dock icon, and VIRTUAL_PANEL_ADD=<p>[:<p>...]
// (for scripts) all go the same way: the batch waits for /status phase
// "ready" (a Dock drop can launch the app; the server refuses a re-insert
// while the unit boots), then GET /samples/add?path=<abs> per file (the
// server copies it into the card's AUDIO pool, converting what is not
// 16/24-bit 44.1 kHz WAV/AIFF), then an alert offers /samples/commit -- the
// card image is rebuilt and the unit rebooted, the way a re-inserted CF card
// is. VIRTUAL_PANEL_ADD_THEN=commit|later answers that alert unattended.
//
// Every alert after launch is a sheet on the window, never runModal(): a
// modal loop entered from a URLSession completion (a block on the main
// queue) leaves the main queue undrained until the click -- other replies,
// the signal handlers and the ready poll all froze (measured 12 Sep 2026).
// And NSApplication.terminate(_:) is refused while a sheet is attached to
// the window (measured the same day: SIGTERM, SIGINT and cmd-Q ignored until
// the sheet was answered), so quit() ends the sheet first and, should
// terminate: still return, stops the server and exits itself.
//
// Audio (12 Sep 2026): with the server's sound on (its port child runs the
// DSP cores, --dsp) the unit's main output is captured; the page monitors
// it through WebAudio (so the web view is configured to play media without
// a user gesture) and every PLAY..STOP is a take on the server
// (/audio/status, /audio.wav?take=N). The Audio menu saves the latest take
// (or the ring when there is no take) through a save panel + URLSession,
// opens the takes folder, and switches the sound on/off (/audio/enable, a
// reboot). The page's own SAVE links (target=_blank, an audio/wav reply)
// are intercepted at the navigation-response stage and become the same
// save flow, so the page never navigates away. VIRTUAL_PANEL_SAVE=<path>,
// VIRTUAL_PANEL_SOUND=0|1, VIRTUAL_PANEL_SAVE_DIR=<dir> and
// VIRTUAL_PANEL_NAV=open:|go:<path> drive it from scripts.
//
// Output device (13 Sep 2026): Audio > Output Device lists the Mac's audio
// devices (GET /audio/devices, fetched when the menu opens) -- Off, then
// each device with a checkmark on the running one -- and sends the choice
// as GET /audio/output?device=<name>: the server streams the unit's outputs
// to it in real time (main L/R on channels 1-2, cue L/R on 3-4, the eight
// tracks as stereo stems on 5-20 -- per-track outputs the hardware does not
// have, O23 -- and the other four ESAI words on 21-24; a device with fewer
// channels gets the first pairs, a 2-channel one main L/R; BlackHole 16ch
// for a DAW carries main, cue and tracks 1-6, the 64ch edition everything).
// The line under the submenu shows the map. The choice is remembered
// (UserDefaults outputDevice) and re-sent whenever /status returns to ready
// (a fresh server after Reload / Open Project knows nothing of it; the
// server itself keeps the stream across its child's reboots and answers
// "already on"). VIRTUAL_PANEL_OUTPUT=<name|off> is this launch's choice,
// not remembered, for scripts.
//
// The card (13 Sep 2026, O19): the server's --card <img> boots a PERSISTENT
// card image with the child's write-back on, so SAVE PROJECT on the unit and
// the samples put on the card survive a quit. File > New Card from Project...
// creates out/cards/<Set>-<Project>.img from a project folder (once) and
// boots it; Open Card... picks an .img; the last card is remembered
// (UserDefaults cardPath) and booted at launch -- with none ever chosen the
// old projectDir behaviour (a fresh per-port image) stays. Show Card in
// Finder reveals the .img. Eject Card = /card/eject: the server flushes,
// stops the child and mounts the image on the Mac; the volume is opened in
// Finder to copy samples / projects / sets in and out; Insert Card =
// /card/insert detaches and boots again. Quit stops the server with SIGTERM,
// which the server now handles: `card flush` + `quit` to the child.
// VIRTUAL_PANEL_CARD=<img> boots that card for one launch (not remembered);
// VIRTUAL_PANEL_PORT_BIN=<bin> passes --port-bin (a build of ot_emu that
// knows --card-rw before out/emu is rebuilt).
import Cocoa
import UniformTypeIdentifiers
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
/// The firmware image the server boots: VIRTUAL_PANEL_IMAGE for scripts, else the remembered
/// File > Open Firmware Image... choice ("imagePath") when the file still exists, else nil = stock.
func firmwareImagePath() -> String? {
    if let e = ProcessInfo.processInfo.environment["VIRTUAL_PANEL_IMAGE"], !e.isEmpty { return e }
    if let p = UserDefaults.standard.string(forKey: "imagePath"), FileManager.default.fileExists(atPath: p) { return p }
    return nil
}

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
    /// With a `card` (O19) the server boots that image as it is (--card); the
    /// project arguments only matter when the image does not exist yet (it
    /// is created from them once). VIRTUAL_PANEL_PORT_BIN adds --port-bin.
    func arguments(project: URL?, card: URL? = nil) -> [String] {
        var a = [repo.appendingPathComponent("tools/panel/panel_server.py").path, "--port", String(port)]
        if let b = ProcessInfo.processInfo.environment["VIRTUAL_PANEL_PORT_BIN"], !b.isEmpty {
            a += ["--port-bin", b]
        }
        if let img = firmwareImagePath() {
            a += ["--image", img]
        }
        if let c = card {
            a += ["--card", c.path]
        }
        if let p = project {
            a += ["--project", p.path,
                  "--set", p.deletingLastPathComponent().lastPathComponent,
                  "--name", p.lastPathComponent]
        }
        return a
    }

    func commandLine(project: URL?, card: URL? = nil) -> String {
        ([repo.appendingPathComponent(".venv/bin/python3").path] + arguments(project: project, card: card)).joined(separator: " ")
    }

    func spawn(project: URL?, card: URL? = nil) throws {
        let p = Process()
        p.executableURL = repo.appendingPathComponent(".venv/bin/python3")   // the arm64 venv (EMAC-fixed Unicorn)
        p.arguments = arguments(project: project, card: card)
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
        Log.write("spawned pid \(p.processIdentifier): \(commandLine(project: project, card: card))")
    }

    /// SIGTERM the spawned server and wait for it (SIGKILL after 5 s). The
    /// server handles SIGTERM since O19: it sends the child `card flush` +
    /// `quit` (the child fsyncs the card image before its ok) and writes the
    /// card's sidecar, then exits -- measured 125 ms on 13 Sep 2026. No-op
    /// for an attached server.
    func stop() {
        guard let p = process else { return }
        process = nil
        guard p.isRunning else { return }
        p.terminate()
        let deadline = Date().addingTimeInterval(5)
        while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL); p.waitUntilExit() }
        Log.write("server pid \(p.processIdentifier) stopped (\(Self.ending(of: p)))")
    }

    /// A server that already answers but runs older code than the repo's
    /// tools/panel/panel_server.py: an orphan from an earlier session (the
    /// 22 Sep 2026 quit crash left one from 15 Sep on the port, and every
    /// launch since attached to it, so a week of server-side fixes never
    /// reached the app). /status carries `script_mtime` since 23 Sep 2026;
    /// no field = older still. Returns why it is stale, nil when current.
    func staleReason(_ status: [String: Any]?) -> String? {
        let path = repo.appendingPathComponent("tools/panel/panel_server.py").path
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: path),
              let mod = attrs[.modificationDate] as? Date else { return nil }
        guard let running = status?["script_mtime"] as? Double else {
            return "it predates 23 Sep 2026 (no script_mtime in /status)"
        }
        if mod.timeIntervalSince1970 > running + 1 {
            let f = DateFormatter(); f.dateFormat = "d MMM HH:mm"
            return "its code is from \(f.string(from: Date(timeIntervalSince1970: running))), the file is from \(f.string(from: mod))"
        }
        return nil
    }

    /// SIGTERM a server this app did not spawn (its pid from /status; else
    /// every panel_server.py on this port). The server flushes the card and
    /// exits, as on quit.
    func terminateForeign(pid: Int) {
        if pid > 1 {
            kill(pid_t(pid), SIGTERM)
            Log.write("sent SIGTERM to the stale server pid \(pid)")
            return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        p.arguments = ["-f", "panel_server.py --port \(port)"]
        try? p.run(); p.waitUntilExit()
        Log.write("pkill -f 'panel_server.py --port \(port)': status \(p.terminationStatus)")
    }

    static let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: c)
    }()

    /// GET /status. ok = HTTP 200 carrying JSON. Completion on the main thread;
    /// a refused connection fails within milliseconds, so a 500 ms poll is cheap.
    func probe(timeout: TimeInterval, _ done: @escaping (Bool, [String: Any]?) -> Void) {
        get("/status", timeout: timeout) { ok, st, _ in done(ok, st) }
    }

    /// Everything but the unreserved set (RFC 3986) and "/" is percent-encoded,
    /// so a path with "&", "=", "+" or "#" in it survives the server's own
    /// split on "&"/"=" and both unquote() and unquote_plus() give it back.
    static let pathAllowed = CharacterSet(charactersIn:
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~/")

    /// GET <path>?<query>, JSON reply. ok = HTTP 200 carrying a JSON object;
    /// `why` says what went wrong otherwise ("HTTP 404: no take 9" when the
    /// error reply is JSON with an "error" -- that object is passed on too
    /// -- else "HTTP 404", or the URL error). Completion on the main thread.
    func get(_ path: String, query: [(String, String)] = [], timeout: TimeInterval,
             _ done: @escaping (Bool, [String: Any]?, String) -> Void) {
        var s = "http://127.0.0.1:\(port)" + path
        if !query.isEmpty {
            s += "?" + query.map { k, v in
                k + "=" + (v.addingPercentEncoding(withAllowedCharacters: Self.pathAllowed) ?? v)
            }.joined(separator: "&")
        }
        guard let u = URL(string: s) else { done(false, nil, "bad URL \(s)"); return }
        let req = URLRequest(url: u, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        Self.session.dataTask(with: req) { data, resp, err in
            var st: [String: Any]? = nil
            var why = ""
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if let d = data, code > 0 {
                st = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any]
            }
            let ok = code == 200 && st != nil
            if ok {
            } else if let e = err {
                why = e.localizedDescription
            } else if code == 200 {
                why = "not JSON: " + String(decoding: (data ?? Data()).prefix(80), as: UTF8.self)
            } else {
                why = "HTTP \(code)" + ((st?["error"] as? String).map { ": " + $0 } ?? "")
            }
            DispatchQueue.main.async { done(ok, st, why) }
        }.resume()
    }

    /// /audio.wav?take=N, or the ring between two absolute frames.
    func audioURL(take: Int? = nil, from: Int? = nil, to: Int? = nil) -> URL {
        var s = "http://127.0.0.1:\(port)/audio.wav"
        if let n = take { s += "?take=\(n)" } else if let f = from, let t = to { s += "?from=\(f)&to=\(t)" }
        return URL(string: s)!
    }

    /// GET `url` to the file `dest` (replaced if it exists; its folder made).
    /// `done(ok, bytes, why, name)` on the main thread: ok = HTTP 200 and the
    /// file in place, else `why` ("HTTP 404: no take 9" -- the server's JSON
    /// error when it sends one -- or the URL / file error); `name` is what
    /// the reply's Content-Disposition suggests (the URL's last component
    /// when there is none). A download task, so a long take never sits in
    /// memory twice.
    func download(_ url: URL, to dest: URL, timeout: TimeInterval,
                  _ done: @escaping (Bool, Int, String, String) -> Void) {
        let req = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        Self.session.downloadTask(with: req) { tmp, resp, err in
            var ok = false, bytes = 0, why = ""
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let name = resp?.suggestedFilename ?? url.lastPathComponent
            if let t = tmp, code == 200 {
                let fm = FileManager.default
                do {
                    try fm.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
                    if fm.fileExists(atPath: dest.path) { try fm.removeItem(at: dest) }
                    try fm.moveItem(at: t, to: dest)   // the temporary file is gone once this block returns
                    try? fm.setAttributes([.posixPermissions: 0o644], ofItemAtPath: dest.path)   // the temp file is 0600
                    bytes = ((try? fm.attributesOfItem(atPath: dest.path))?[.size] as? NSNumber)?.intValue ?? 0
                    ok = true
                } catch {
                    why = "could not write \(dest.path): \(error.localizedDescription)"
                }
            } else if let e = err {
                why = e.localizedDescription
            } else {
                why = "HTTP \(code)"
                if let t = tmp, let d = try? Data(contentsOf: t),
                   let j = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
                   let e = j["error"] as? String { why += ": " + e }
            }
            DispatchQueue.main.async { done(ok, bytes, why, name) }
        }.resume()
    }
}

// MARK: - the web view (a drop target)

/// WKWebView that takes audio files dropped on the window for the sample
/// pool instead of letting the page (or WebKit's default navigation) have
/// them. Every other drag -- text into the key-map drawer's fields -- goes
/// to super, i.e. the page, as before.
final /// A key the page does not consume falls off the responder chain and the
/// window's noResponder(for:) beeps on every auto-repeat (a held Z sounded
/// the alert continuously). The panel has no text fields: stay silent.
class PanelWindow: NSWindow {
    override func noResponder(for eventSelector: Selector) {
        if eventSelector != #selector(NSResponder.keyDown(with:)) { super.noResponder(for: eventSelector) }
    }
}

class PanelWebView: WKWebView {
    var onDropFiles: (([URL]) -> Void)?

    override init(frame: CGRect, configuration: WKWebViewConfiguration) {
        super.init(frame: frame, configuration: configuration)
        registerForDraggedTypes(registeredDraggedTypes + [.fileURL])
    }
    required init?(coder: NSCoder) { fatalError("not used") }

    private func droppedFiles(_ info: NSDraggingInfo) -> [URL] {
        let urls = info.draggingPasteboard.readObjects(forClasses: [NSURL.self],
                                                       options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? []
        return urls.filter { AppDelegate.isSampleSource($0) }
    }

    override func draggingEntered(_ info: NSDraggingInfo) -> NSDragOperation {
        droppedFiles(info).isEmpty ? super.draggingEntered(info) : .copy
    }
    override func draggingUpdated(_ info: NSDraggingInfo) -> NSDragOperation {
        droppedFiles(info).isEmpty ? super.draggingUpdated(info) : .copy
    }
    override func prepareForDragOperation(_ info: NSDraggingInfo) -> Bool {
        droppedFiles(info).isEmpty ? super.prepareForDragOperation(info) : true
    }
    override func performDragOperation(_ info: NSDraggingInfo) -> Bool {
        let urls = droppedFiles(info)
        if urls.isEmpty { return super.performDragOperation(info) }
        onDropFiles?(urls)
        return true
    }
}

// MARK: - the app

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate,
                         WKUIDelegate, NSMenuDelegate {
    static let projectKey = "projectDir"   // UserDefaults: the Open Project... choice
    static let saveDirKey = "saveDir"      // UserDefaults: where the last recording was saved
    static let cardKey = "cardPath"        // UserDefaults: the card image (O19) booted at launch
    static let outputKey = "outputDevice"  // UserDefaults: the output device's name ("" / absent = off)
    static let imageKey = "imagePath"      // UserDefaults: a firmware image (a remix .bin) instead of the stock one (14 Sep 2026)
    static var imagePath: String? { firmwareImagePath() }

    let repo: URL
    let server: PanelServer
    var window: NSWindow!
    var web: WKWebView!
    var poll: Timer?
    var probing = false
    var probeGen = 0                       // startServer probes older than this are dropped
    var panelShown = false                 // the web view is on the server's page
    var signalSources: [DispatchSourceSignal] = []
    /// One add batch as it was asked for; queued whole so a VIRTUAL_PANEL_ADD
    /// that arrives before the server answers keeps its auto-answer (the
    /// first version queued bare URLs and re-ran them with the alert).
    struct AddRequest { let urls: [URL]; let source: String; let autoAnswer: String? }
    var pendingAdds: [AddRequest] = []     // batches waiting for the unit (or for a running batch)
    var adding = false                     // an add batch (ready wait, requests, alert, commit) is in flight
    var quitting = false                   // quit() has begun: sheet handlers and queued batches do nothing
    var shutDown = false                   // shutdown() ran (once, whichever path quits)
    // audio (12 Sep 2026)
    var soundItem: NSMenuItem!             // Audio > Sound: a checkbox that follows /status "sound"
    var soundOn: Bool?                     // /status "sound" as last polled; nil = no answer / no such field
    var soundNote = ""                     // /status "sound_note"
    var phase = ""                         // /status "phase" as last polled
    var statusPoll: Timer?                 // the 2 s /status poll behind the Sound checkbox
    var statusProbing = false
    var saveDir: URL?                      // VIRTUAL_PANEL_SAVE_DIR: saves land there, no save panel
    var navHook = ""                       // VIRTUAL_PANEL_NAV: fired once the page has loaded
    // the card (O19)
    var cardItem: NSMenuItem!              // File > Eject Card / Insert Card (the title follows /status)
    var showCardItem: NSMenuItem!          // File > Show Card in Finder (enabled with a persistent card)
    var cardPathFromStatus: String?        // /status "card"
    var cardPersistent = false             // /status "card_mode" == persistent
    var cardEjected = false                // /status "card_ejected"
    var cardMount: String?                 // /status "card_mount"
    var cardRw: Bool?                      // /status "card_rw"
    var launchCard: URL?                   // VIRTUAL_PANEL_CARD: this launch's card, not remembered
    var navHookMarker = ""
    var interceptedNavs = 0                // audio/wav navigations turned into the save flow
    // the output device (13 Sep 2026)
    var audioMenu: NSMenu!
    var outputMenu: NSMenu!                // Audio > Output Device: Off + every device /audio/devices lists
    var outputMapItem: NSMenuItem!         // the channel-map line under it (disabled, informative)
    var outputDevices: [[String: Any]] = []   // /audio/devices as last fetched
    var outputState: [String: Any]?        // the server's "output" state as last fetched
    var outputRefreshing = false
    var launchOutput: String?              // VIRTUAL_PANEL_OUTPUT: this launch's device, not remembered
    var readyBefore = false                // /status has read ready once: later readies are "ready again"

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
        // VIRTUAL_PANEL_CARD=<img>: this launch boots that card (created from
        // the project if missing), the remembered choice untouched.
        if let c = ProcessInfo.processInfo.environment["VIRTUAL_PANEL_CARD"], !c.isEmpty {
            launchCard = URL(fileURLWithPath: c)
            Log.write("VIRTUAL_PANEL_CARD: \(c) (this launch only)")
        }
        startServer()
        // VIRTUAL_PANEL_ADD=<path>[:<path>...]: the add flow without the UI,
        // with VIRTUAL_PANEL_ADD_THEN=commit|later standing in for the alert
        // (unset, empty or anything else: the alert is shown).
        let env = ProcessInfo.processInfo.environment
        if let spec = env["VIRTUAL_PANEL_ADD"], !spec.isEmpty {
            let urls = spec.split(separator: ":").map { URL(fileURLWithPath: String($0)) }
            var then = env["VIRTUAL_PANEL_ADD_THEN"] ?? ""
            if !then.isEmpty && then != "commit" && then != "later" {
                Log.write("VIRTUAL_PANEL_ADD_THEN=\(then): neither commit nor later, the alert is shown instead")
                then = ""
            }
            Log.write("VIRTUAL_PANEL_ADD: \(urls.count) path(s), then \(then.isEmpty ? "the alert" : then)")
            addSamples(urls, source: "VIRTUAL_PANEL_ADD", autoAnswer: then.isEmpty ? nil : then)
        }
        drainPendingAdds()   // a Dock-drop launch queued its files before the window existed
        // Audio hooks, for scripts (menus need the Accessibility grant to drive):
        // VIRTUAL_PANEL_SAVE_DIR=<dir> answers every save panel unattended (the
        // file lands there under the suggested name); VIRTUAL_PANEL_SAVE=<path>
        // saves the latest take there once the unit is ready and a take exists
        // (VIRTUAL_PANEL_SAVE_WAIT=<s> bounds that wait, default 600);
        // VIRTUAL_PANEL_SOUND=0|1 switches the sound once ready, no sheet;
        // VIRTUAL_PANEL_NAV=open:<path>|go:<path> makes the page window.open()
        // / navigate to <path> once loaded, to exercise the interception.
        if let d = env["VIRTUAL_PANEL_SAVE_DIR"], !d.isEmpty {
            saveDir = URL(fileURLWithPath: d, isDirectory: true)
            Log.write("VIRTUAL_PANEL_SAVE_DIR: saves go to \(d) without the panel")
        }
        if let p = env["VIRTUAL_PANEL_SAVE"], !p.isEmpty {
            let wait = Double(env["VIRTUAL_PANEL_SAVE_WAIT"] ?? "") ?? 600
            Log.write("VIRTUAL_PANEL_SAVE: \(p) (waiting up to \(Int(wait)) s for a take once ready)")
            saveHook(URL(fileURLWithPath: p), wait: wait)
        }
        if let s = env["VIRTUAL_PANEL_SOUND"], !s.isEmpty {
            if s == "0" || s == "1" {
                let on = s == "1"
                Log.write("VIRTUAL_PANEL_SOUND: \(on ? "on" : "off") once ready")
                whenReady(deadline: Date().addingTimeInterval(900), what: "VIRTUAL_PANEL_SOUND") { [weak self] ok in
                    guard let self = self, !self.quitting else { return }
                    if ok { self.setSound(on, source: "VIRTUAL_PANEL_SOUND", quiet: true) }
                    else { Log.write("VIRTUAL_PANEL_SOUND: the unit is not ready, nothing switched") }
                }
            } else {
                Log.write("VIRTUAL_PANEL_SOUND=\(s): neither 0 nor 1, ignored")
            }
        }
        if let n = env["VIRTUAL_PANEL_NAV"], !n.isEmpty {
            navHook = n
            Log.write("VIRTUAL_PANEL_NAV: \(n), fired once the page has loaded")
        }
        // VIRTUAL_PANEL_OUTPUT=<name|off>: this launch's output device, not
        // remembered, applied whenever /status returns to ready (like the
        // remembered choice).
        if let o = env["VIRTUAL_PANEL_OUTPUT"], !o.isEmpty {
            launchOutput = o
            Log.write("VIRTUAL_PANEL_OUTPUT: \(o) (this launch only; applied whenever the unit is ready)")
        }
        startStatusPoll()
    }

    /// Files handed to the app by Finder: dropped on the Dock icon, or opened
    /// with it. Called before the window exists when the app is launched by
    /// the drop, hence the queue.
    func application(_ app: NSApplication, open urls: [URL]) {
        addSamples(urls, source: "Dock / Finder")
    }

    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool { true }
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }

    /// Also reached by the Dock's Quit and an AppleScript quit, which call
    /// terminate: directly (with a sheet up those are refused before this
    /// runs -- as in every Cocoa app; the app's own routes end the sheet
    /// first, see quit()).
    /// "Are you sure you want to quit?" (15 Sep 2026, the owner's ask): every
    /// user-driven quit asks first -- the window's close box BEFORE the window
    /// closes (windowShouldClose: closing it first and then declining left the
    /// app alive without a window, and it crashed on the next update), and
    /// cmd-Q / the Dock / the Quit item in applicationShouldTerminate. A quit
    /// driven by a signal (a script's SIGTERM, ctrl-C) never asks.
    var quitFromSignal = false
    var quitConfirmed = false
    var quitDeclined = false
    func askQuit() -> Bool {
        let a = NSAlert()
        a.messageText = "Are you sure you want to quit?"
        a.informativeText = "The unit stops; the card and its project stay as they are."
        a.alertStyle = .warning
        a.addButton(withTitle: "Yes")
        a.addButton(withTitle: "No")
        if let sheet = window?.attachedSheet { window.endSheet(sheet, returnCode: .cancel) }
        return a.runModal() == .alertFirstButtonReturn
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if quitFromSignal || quitConfirmed { return true }
        if askQuit() { quitConfirmed = true; return true }
        Log.write("quit (close box): declined")
        return false
    }
    func applicationShouldTerminate(_ s: NSApplication) -> NSApplication.TerminateReply {
        if !quitFromSignal && !quitConfirmed {
            if !askQuit() {
                quitDeclined = true
                quitting = false
                Log.write("quit: declined")
                return .terminateCancel
            }
            quitConfirmed = true
        }
        quitting = true
        return .terminateNow
    }

    func applicationWillTerminate(_ n: Notification) { shutdown() }

    /// Stop what the app started; once, whichever way the process ends.
    func shutdown() {
        guard !shutDown else { return }
        shutDown = true
        stopPolling()
        statusPoll?.invalidate()
        statusPoll = nil
        server.stop()
        Log.write("quit")
    }

    /// Quit Virtual Panel (cmd-Q), SIGTERM/SIGINT/SIGHUP. terminate: is
    /// refused while a sheet is attached to the window (measured 12 Sep 2026
    /// with the "N files added" sheet: SIGTERM, SIGINT and cmd-Q ignored until
    /// it was answered, SIGUSR1 handled in 42 ms meanwhile), so the sheet is
    /// ended first, as Cancel / Later. terminate: exits the process when it
    /// goes through; if it returns anyway the server is stopped here and the
    /// process exits directly, so a kill never has to be repeated.
    @objc func quit(_ sender: Any?) {
        quitting = true
        quitDeclined = false
        if let s = window?.attachedSheet {
            Log.write("quit: ending the open sheet")
            window.endSheet(s, returnCode: .cancel)
        }
        NSApp.terminate(nil)
        if quitDeclined { quitDeclined = false; return }   // the user said No
        Log.write("quit: terminate: returned (refused); stopping the server and exiting directly")
        shutdown()
        exit(0)
    }

    /// SIGTERM/SIGINT/SIGHUP (kill, ctrl-C in the shell that launched it,
    /// terminal closed) go through quit() so the server is stopped too.
    /// SIGKILL cannot be caught: that one orphans a spawned server (pkill -f
    /// panel_server.py). SIGUSR1 is File > Reload and SIGUSR2 File > Show
    /// Card Audio Folder, for scripts (menus cannot be driven without the
    /// Accessibility grant).
    func installSignalHandlers() {
        let quit: () -> Void = { [weak self] in self?.quitFromSignal = true; self?.quit(nil) }
        let reload: () -> Void = { [weak self] in self?.reload(nil) }
        let showPool: () -> Void = { [weak self] in self?.showCardAudioFolder(nil) }
        for (sig, act) in [(SIGTERM, quit), (SIGINT, quit), (SIGHUP, quit), (SIGUSR1, reload), (SIGUSR2, showPool)] {
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
        window = PanelWindow(contentRect: rect,
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
        // The page's monitor plays the unit's main output through WebAudio;
        // its AudioContext is created on the headphones click, and WebKit
        // must not demand a further gesture for playback. (Inline playback
        // is macOS's only mode: allowsInlineMediaPlayback is an iOS setting.)
        let cfg = WKWebViewConfiguration()
        cfg.mediaTypesRequiringUserActionForPlayback = []
        let pw = PanelWebView(frame: rect, configuration: cfg)
        pw.onDropFiles = { [weak self] urls in self?.addSamples(urls, source: "window drop") }
        web = pw
        web.autoresizingMask = [.width, .height]
        web.navigationDelegate = self
        web.uiDelegate = self   // target=_blank / window.open (the page's SAVE links), JS alert/confirm
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
        app.addItem(withTitle: "Quit Virtual Panel", action: #selector(quit(_:)), keyEquivalent: "q").target = self
        appItem.submenu = app

        let fileItem = NSMenuItem(); main.addItem(fileItem)
        let file = NSMenu(title: "File")
        file.delegate = self
        // the card (O19): a persistent image the unit saves into
        file.addItem(withTitle: "New Card from Project...", action: #selector(newCardFromProject(_:)), keyEquivalent: "n").target = self
        let openCard = file.addItem(withTitle: "Open Card...", action: #selector(openCard(_:)), keyEquivalent: "O")
        file.addItem(withTitle: "Open Firmware Image...", action: #selector(openImage(_:)), keyEquivalent: "i").target = self
        file.addItem(withTitle: "Use Stock Firmware", action: #selector(useStockImage(_:)), keyEquivalent: "").target = self
        openCard.keyEquivalentModifierMask = [.command, .shift]
        openCard.target = self
        showCardItem = file.addItem(withTitle: "Show Card in Finder", action: #selector(showCardInFinder(_:)), keyEquivalent: "")
        showCardItem.target = self
        cardItem = file.addItem(withTitle: "Eject Card", action: #selector(ejectOrInsertCard(_:)), keyEquivalent: "e")
        cardItem.target = self
        cardItem.isEnabled = false
        file.addItem(.separator())
        // the old way: a project folder onto a fresh per-port card (nothing persists)
        file.addItem(withTitle: "Open Project (scratch card)...", action: #selector(openProject(_:)), keyEquivalent: "o").target = self
        file.addItem(withTitle: "Reload", action: #selector(reload(_:)), keyEquivalent: "r").target = self
        file.addItem(.separator())
        let add = file.addItem(withTitle: "Add Samples to Card...", action: #selector(addSamples(_:)), keyEquivalent: "A")
        add.keyEquivalentModifierMask = [.command, .shift]
        add.target = self
        file.addItem(withTitle: "Show Card Audio Folder", action: #selector(showCardAudioFolder(_:)), keyEquivalent: "").target = self
        fileItem.submenu = file

        // Edit: the panel's key-map drawer has text fields and EXPORT MAP uses the clipboard.
        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        // Audio: the unit's main output as the server captures it. Items are
        // enabled by hand (autoenablesItems off): Sound follows /status.
        let audioItem = NSMenuItem(); main.addItem(audioItem)
        let audio = NSMenu(title: "Audio")
        audio.autoenablesItems = false
        audio.delegate = self   // menuWillOpen: a fresh /status before the checkbox is seen
        let save = audio.addItem(withTitle: "Save Main Out Recording...", action: #selector(saveMainOut(_:)), keyEquivalent: "S")
        save.keyEquivalentModifierMask = [.command, .shift]
        save.target = self
        audio.addItem(withTitle: "Show Takes Folder", action: #selector(showTakesFolder(_:)), keyEquivalent: "").target = self
        audio.addItem(.separator())
        soundItem = audio.addItem(withTitle: "Sound (DSP audio, slower sequencer)", action: #selector(toggleSound(_:)), keyEquivalent: "")
        soundItem.target = self
        soundItem.isEnabled = false
        soundItem.toolTip = "The port child runs the DSP cores (--dsp): the main output is captured for the page's monitor and the takes; while it plays the unit runs ~9x slower than real time (~3x slower than without the cores). Switching reboots the unit."
        audio.addItem(.separator())
        // Output Device (13 Sep 2026): the unit's outputs on a Mac audio device
        // (BlackHole for a DAW); the list comes from /audio/devices when the
        // Audio menu or the submenu opens, the checkmark from the same reply.
        let outItem = audio.addItem(withTitle: "Output Device", action: nil, keyEquivalent: "")
        outputMenu = NSMenu(title: "Output Device")
        outputMenu.autoenablesItems = false
        outputMenu.delegate = self
        outItem.submenu = outputMenu
        outputMapItem = audio.addItem(withTitle: Self.mapLine(nil), action: nil, keyEquivalent: "")
        outputMapItem.isEnabled = false
        outputMapItem.toolTip = "Where the unit's outputs land on the chosen device: main L/R on channels 1-2, cue L/R on 3-4, tracks 1-8 as stereo pairs on 5-20 (T1 on 5-6 ... T8 on 19-20), ESAI words 0/1 on 21-22 and 6/7 on 23-24 (as many pairs as the device has; a 2-channel device gets main L/R, BlackHole 16ch main, cue and tracks 1-6)."
        rebuildOutputMenu()
        audioMenu = audio
        audioItem.submenu = audio

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

    /// The card to boot (O19): VIRTUAL_PANEL_CARD for this launch, else the
    /// remembered New Card / Open Card choice (a remembered file that is gone
    /// is forgotten and logged); nil = the old behaviour (projectDir() on a
    /// fresh per-port card).
    func cardPath() -> URL? {
        if let c = launchCard { return c }
        if let p = UserDefaults.standard.string(forKey: Self.cardKey) {
            if FileManager.default.fileExists(atPath: p) { return URL(fileURLWithPath: p) }
            Log.write("remembered card is gone, ignoring: \(p)")
            UserDefaults.standard.removeObject(forKey: Self.cardKey)
        }
        return nil
    }

    /// out/cards/<Set>-<Project>.img for a project folder (its parent is the set).
    func defaultCard(for project: URL) -> URL {
        let set = project.deletingLastPathComponent().lastPathComponent
        return repo.appendingPathComponent("out/cards/\(set)-\(project.lastPathComponent).img")
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
            if ok, let why = self.server.staleReason(status) {
                // an orphan running old code: retire it and start a fresh one
                Log.write("the server on port \(self.server.port) is stale (\(why)): restarting it; status \(status ?? [:])")
                self.showPlaceholder("restarting the panel server on port \(self.server.port): \(why)")
                self.server.terminateForeign(pid: (status?["pid"] as? Int) ?? -1)
                self.whenServerGone(gen: gen, tries: 60) { [weak self] in
                    guard let self = self, gen == self.probeGen else { return }
                    let card = self.cardPath()
                    let project = card.map { FileManager.default.fileExists(atPath: $0.path) ? nil : self.projectDir() } ?? self.projectDir()
                    self.launch(project: project, card: card)
                }
                return
            }
            if ok {
                self.server.attached = true
                Log.write("a server already answers on port \(self.server.port): attaching (it stays up on quit); status \(status ?? [:])")
                self.loadPanel()
                return
            }
            let card = self.cardPath()
            // a card that exists boots as it is; a missing one (VIRTUAL_PANEL_CARD
            // naming a new file) is created from the project the old default gives
            let project = card.map { FileManager.default.fileExists(atPath: $0.path) ? nil : self.projectDir() } ?? self.projectDir()
            self.launch(project: project, card: card)
        }
    }

    /// Poll until nothing answers on the port (the retired server has exited),
    /// then `then`; gives up after `tries` x 0.25 s and proceeds anyway (the
    /// spawn's bind failure then says so in the log).
    func whenServerGone(gen: Int, tries: Int, then: @escaping () -> Void) {
        server.probe(timeout: 0.3) { [weak self] ok, _ in
            guard let self = self, gen == self.probeGen else { return }
            if !ok || tries <= 0 {
                if ok { Log.write("the stale server on port \(self.server.port) is still answering; spawning anyway") }
                then(); return
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { self.whenServerGone(gen: gen, tries: tries - 1, then: then) }
        }
    }

    func launch(project: URL?, card: URL? = nil) {
        var what = project.map { "project \($0.deletingLastPathComponent().lastPathComponent)/\($0.lastPathComponent)" } ?? "empty card"
        if let c = card { what = "card \(c.lastPathComponent)" + (project == nil ? "" : " (new, from \(what))") }
        do {
            try server.spawn(project: project, card: card)
        } catch {
            Log.write("spawn failed: \(error)")
            showPlaceholder("could not start the panel server: \(error.localizedDescription) -- is .venv built (scripts/setup.sh)?", failed: true)
            return
        }
        showPlaceholder("starting the panel server on port \(server.port) (\(what))")
        startPolling()
    }

    /// Open Project... / New Card / Open Card: a spawned server is replaced;
    /// an attached one is not ours to restart, so say what to run instead.
    func restartServer(project: URL?, card: URL? = nil) {
        if server.attached {
            sheet("The server on port \(server.port) was not started by this app",
                  "Restart it yourself with the project, then choose File > Reload:\n\n"
                  + server.commandLine(project: project, card: card))
            return
        }
        stopPolling()
        probeGen += 1          // drop a startServer probe still in flight
        server.stop()
        panelShown = false
        launch(project: project, card: card)
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

    /// Poll /status once a second until phase is "ready" (true), or the
    /// server says failed / the deadline has passed (false). The phase is
    /// logged under `what` each time it changes, "no answer" while nothing
    /// listens on the port yet. Main thread.
    func whenReady(deadline: Date, what: String, seen: String = "", _ done: @escaping (Bool) -> Void) {
        server.probe(timeout: 2.0) { [weak self] ok, st in
            guard let self = self else { return }
            let phase = ok ? (st?["phase"] as? String ?? "?") : "no answer"
            if phase == "ready" { done(true); return }
            if phase == "failed" { Log.write("\(what): the server failed: \(st?["fault"] ?? "")"); done(false); return }
            if phase != seen { Log.write("\(what): waiting for phase ready (now: \(phase))") }
            if Date() > deadline { Log.write("\(what): still not ready, giving up"); done(false); return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
                self.whenReady(deadline: deadline, what: what, seen: phase, done)
            }
        }
    }

    /// An alert as a sheet on the window; `done` gets the index of the
    /// button pressed (0 = the first). Never runModal() after launch: a modal
    /// loop entered from a URLSession completion (a block on the main queue)
    /// leaves the main queue undrained until the click -- other replies, the
    /// ready poll and the signal handlers all waited on the commit-failure
    /// alert (measured 12 Sep 2026). Sheets on one window queue up; nothing
    /// is shown once quitting, and a sheet quit() ended gets no `done`.
    func sheet(_ title: String, _ info: String, buttons: [String] = ["OK"],
               _ done: @escaping (Int) -> Void = { _ in }) {
        guard !quitting else { return }
        let a = NSAlert()
        a.messageText = title
        a.informativeText = info
        for b in buttons { a.addButton(withTitle: b) }
        a.beginSheetModal(for: window) { [weak self] resp in
            guard let self = self, !self.quitting else { return }
            done(max(0, resp.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue))
        }
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
            UserDefaults.standard.removeObject(forKey: Self.cardKey)   // back to the scratch card until a card is chosen again
            self.launchCard = nil
            Log.write("project chosen (remembered; the card forgotten): \(dir.path)")
            self.restartServer(project: dir)
        }
    }

    // MARK: the card (O19)

    /// New Card from Project...: a project folder saved on a unit -> a card
    /// image out/cards/<Set>-<Project>.img (created by the server once, from
    /// the project and its sibling AUDIO), remembered and booted. An image
    /// of that name that exists is offered as it is (Open) or replaced.
    @objc func newCardFromProject(_ sender: Any?) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Make Card"
        panel.message = "A project folder saved on a unit (SET/PROJECT): a card image is made from it under out/cards/ and booted. The unit then saves into that card."
        let projects = repo.appendingPathComponent("out/_projects")
        if FileManager.default.fileExists(atPath: projects.path) { panel.directoryURL = projects }
        panel.beginSheetModal(for: window) { [weak self] resp in
            guard let self = self, resp == .OK, let dir = panel.url else { return }
            let card = self.defaultCard(for: dir)
            if FileManager.default.fileExists(atPath: card.path) {
                self.sheet("A card for this project already exists",
                           "\(card.path)\n\nOpen it as it is (what the unit saved into it stays), or replace it with a fresh card from the project folder?",
                           buttons: ["Open Existing", "Replace", "Cancel"]) { b in
                    if b == 2 { return }
                    if b == 1 {
                        for suffix in ["", ".json"] {
                            try? FileManager.default.removeItem(atPath: card.path + suffix)
                        }
                        Log.write("card replaced: \(card.path)")
                    }
                    self.useCard(card, project: b == 1 ? dir : nil, source: "New Card from Project")
                }
                return
            }
            self.useCard(card, project: dir, source: "New Card from Project")
        }
    }

    /// Open Card...: an existing .img (its sidecar, or the sets on it, name the project).
    @objc func openCard(_ sender: Any?) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Insert"
        panel.message = "A card image (.img) made by Virtual Panel, or any MBR + FAT16 card image with a set on it"
        if #available(macOS 11.0, *) {
            panel.allowedContentTypes = [UTType(filenameExtension: "img") ?? .data, .diskImage, .data]
        }
        let cards = repo.appendingPathComponent("out/cards")
        if FileManager.default.fileExists(atPath: cards.path) { panel.directoryURL = cards }
        panel.beginSheetModal(for: window) { [weak self] resp in
            guard let self = self, resp == .OK, let img = panel.url else { return }
            self.useCard(img, project: nil, source: "Open Card")
        }
    }

    /// Open Firmware Image...: a remix .bin (REMIX=<name> make bus -> out/mainos_bus.bin)
    /// instead of the stock OS; remembered, the unit reboots on it (14 Sep 2026).
    @objc func openImage(_ sender: Any?) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Boot"
        panel.message = "A main-OS image to boot instead of the stock one: a remix built by octabam (out/mainos_bus.bin, or a copy of it)"
        if #available(macOS 11.0, *) {
            panel.allowedContentTypes = [UTType(filenameExtension: "bin") ?? .data, .data]
        }
        let out = repo.appendingPathComponent("out")
        if FileManager.default.fileExists(atPath: out.path) { panel.directoryURL = out }
        panel.beginSheetModal(for: window) { [weak self] resp in
            guard let self = self, resp == .OK, let img = panel.url else { return }
            UserDefaults.standard.set(img.path, forKey: Self.imageKey)
            Log.write("firmware image chosen (remembered): \(img.path)")
            self.rebootOnCurrentCard()
        }
    }

    @objc func useStockImage(_ sender: Any?) {
        UserDefaults.standard.removeObject(forKey: Self.imageKey)
        Log.write("firmware image: back to the stock image")
        rebootOnCurrentCard()
    }

    /// Restart the server on whatever card/project it runs on now (the image comes from imagePath).
    func rebootOnCurrentCard() {
        if let c = UserDefaults.standard.string(forKey: Self.cardKey), FileManager.default.fileExists(atPath: c) {
            restartServer(project: nil, card: URL(fileURLWithPath: c))
        } else {
            restartServer(project: projectDir(), card: nil)
        }
    }

    /// Remember the card and restart the server on it (`project` only when
    /// the image is to be created from it).
    func useCard(_ card: URL, project: URL?, source: String) {
        UserDefaults.standard.set(card.path, forKey: Self.cardKey)
        launchCard = nil
        Log.write("card chosen (\(source), remembered): \(card.path)" + (project.map { " from \($0.path)" } ?? ""))
        restartServer(project: project, card: card)
    }

    /// Show Card in Finder: the .img the server runs on (/status "card").
    @objc func showCardInFinder(_ sender: Any?) {
        guard let c = cardPathFromStatus, cardPersistent else {
            sheet("No card file", "The unit runs on a scratch card (a fresh per-port image). File > New Card from Project... makes one that persists.")
            return
        }
        Log.write("show card: \(c)")
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: c)])
    }

    /// Eject Card / Insert Card (the one item, its title from /status):
    /// eject = GET /card/eject?open=0 (the server flushes, stops the child,
    /// mounts the image), then the volume opened in Finder here; insert =
    /// GET /card/insert (the server cleans macOS's droppings, detaches,
    /// boots). Refusals are sheets with the server's error.
    @objc func ejectOrInsertCard(_ sender: Any?) {
        if cardEjected {
            Log.write("card: GET /card/insert")
            server.get("/card/insert", timeout: 30) { [weak self] ok, r, why in
                guard let self = self else { return }
                if ok, let r = r, r["ok"] as? Bool == true {
                    Log.write("card inserted: \(r["phase"] as? String ?? "ok")")
                } else {
                    let e = (r?["error"] as? String) ?? why
                    Log.write("card insert failed: \(e)")
                    self.sheet("The card could not be inserted", e + "\n\nClose what holds files on the volume (a Finder window, Quick Look) and try again.")
                }
                self.statusProbing = false
                self.pollStatusOnce()
            }
            return
        }
        sheet("Eject the card?",
              "The unit is switched off and its card is mounted on this Mac (the volume opens in Finder): copy samples into <SET>/AUDIO, projects into <SET>/, or whole sets. File > Insert Card puts it back and boots the unit (~40 s). Save your project on the unit first (PROJECT > SAVE) if it matters -- as with a real card.",
              buttons: ["Eject", "Cancel"]) { [weak self] b in
            guard b == 0, let self = self else { Log.write("card eject: cancelled"); return }
            Log.write("card: GET /card/eject?open=0")
            self.server.get("/card/eject", query: [("open", "0")], timeout: 30) { [weak self] ok, r, why in
                guard let self = self else { return }
                if ok, let r = r, r["ok"] as? Bool == true {
                    Log.write("card ejecting: \(r["phase"] as? String ?? "ok")")
                    self.openMountWhenEjected(deadline: Date().addingTimeInterval(60))
                } else {
                    let e = (r?["error"] as? String) ?? why
                    Log.write("card eject failed: \(e)")
                    self.sheet("The card could not be ejected", e)
                }
            }
        }
    }

    /// The eject is an emulator-thread action (flush, stop, hdiutil): poll
    /// /status until card_ejected, then open the mount point in Finder.
    func openMountWhenEjected(deadline: Date) {
        server.probe(timeout: 2.0) { [weak self] ok, st in
            guard let self = self, !self.quitting else { return }
            if ok, let st = st, st["card_ejected"] as? Bool == true, let m = st["card_mount"] as? String {
                Log.write("card ejected, mounted at \(m): opening in Finder")
                NSWorkspace.shared.open(URL(fileURLWithPath: m, isDirectory: true))
                self.noteStatus(st)
                return
            }
            if ok, let f = st?["fault"] as? String, f.hasPrefix("card eject") {
                Log.write("card eject: \(f)")
                self.sheet("The card could not be ejected", f)
                return
            }
            if Date() > deadline { Log.write("card eject: not ejected after 60 s"); return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.openMountWhenEjected(deadline: deadline) }
        }
    }

    func updateCardItems() {
        guard let item = cardItem else { return }
        let title = cardEjected ? "Insert Card" : "Eject Card"
        let enabled = cardPersistent && !quitting && (cardEjected || phase == "ready")
        if title != item.title || enabled != item.isEnabled {
            Log.write("card item: \(title), \(enabled ? "enabled" : "disabled")")
        }
        item.title = title
        item.isEnabled = enabled
        showCardItem?.isEnabled = cardPersistent && cardPathFromStatus != nil
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

    // MARK: samples

    /// The extensions the open panel lists by name, beside every type that
    /// conforms to public.audio: what the unit plays as is (WAV/AIFF) and the
    /// usual ones the server converts with afconvert.
    static let sampleExtensions = ["wav", "aif", "aiff", "mp3", "m4a", "flac", "ogg"]

    /// What is sent to the server: one of the extensions above, or any file
    /// whose type conforms to public.audio (caf, aac, aifc, mp2 ... -- the
    /// panel lets those be picked and the server's pool converts whatever
    /// afconvert reads). The type comes from the file itself when it can be
    /// read, else from the extension.
    static func isAudioFile(_ u: URL) -> Bool {
        if sampleExtensions.contains(u.pathExtension.lowercased()) { return true }
        let t = (try? u.resourceValues(forKeys: [.contentTypeKey]))?.contentType
            ?? UTType(filenameExtension: u.pathExtension)
        return t?.conforms(to: .audio) ?? false
    }

    /// An audio file, or a folder (its audio files are taken, one level deep).
    static func isSampleSource(_ u: URL) -> Bool {
        var dir: ObjCBool = false
        if FileManager.default.fileExists(atPath: u.path, isDirectory: &dir), dir.boolValue { return true }
        return isAudioFile(u)
    }

    /// Folders expanded, non-audio files listed under `skipped`.
    static func sampleFiles(_ urls: [URL]) -> (files: [URL], skipped: [URL]) {
        var files: [URL] = [], skipped: [URL] = []
        let fm = FileManager.default
        for u in urls {
            var dir: ObjCBool = false
            if fm.fileExists(atPath: u.path, isDirectory: &dir), dir.boolValue {
                let kids = (try? fm.contentsOfDirectory(at: u, includingPropertiesForKeys: [.contentTypeKey],
                                                        options: [.skipsHiddenFiles])) ?? []
                files += kids.filter { isAudioFile($0) }
                            .sorted { $0.lastPathComponent.lowercased() < $1.lastPathComponent.lowercased() }
            } else if isAudioFile(u) {
                files.append(u)
            } else {
                skipped.append(u)
            }
        }
        return (files, skipped)
    }

    @objc func addSamples(_ sender: Any?) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.prompt = "Add"
        panel.message = "Audio files for the card's AUDIO folder (WAV/AIFF go as they are; mp3, m4a, flac, ogg and other rates are converted to 16-bit 44.1 kHz WAV)"
        panel.allowedContentTypes = Self.sampleExtensions.compactMap { UTType(filenameExtension: $0) } + [.audio]
        panel.beginSheetModal(for: window) { [weak self] resp in
            guard let self = self, resp == .OK, !panel.urls.isEmpty else { return }
            self.addSamples(panel.urls, source: "Add Samples to Card")
        }
    }

    /// The one add flow, whatever brought the files: the batch is queued and
    /// run when the unit is ready, one batch at a time (each has its own
    /// alert or answer). `autoAnswer` "commit" / "later" replaces the alert
    /// (VIRTUAL_PANEL_ADD_THEN); nil shows it.
    func addSamples(_ urls: [URL], source: String, autoAnswer: String? = nil) {
        Log.write("add (\(source)): \(urls.count) path(s)\(adding ? ", queued behind the running batch" : "")")
        pendingAdds.append(AddRequest(urls: urls, source: source, autoAnswer: autoAnswer))
        drainPendingAdds()
    }

    /// Start the next queued batch once /status says ready -- not before: a
    /// Dock drop can launch the app (no server yet), files dropped during
    /// the boot used to be added at once and then offered a re-insert the
    /// server refuses ("the unit is still booting", measured 12 Sep 2026),
    /// and adds are refused while a re-insert runs. `adding` holds from here
    /// until the batch's alert, and the commit it may start, are done.
    func drainPendingAdds() {
        guard window != nil, !adding, !quitting, !pendingAdds.isEmpty else { return }
        adding = true
        let r = pendingAdds.removeFirst()
        let what = "add (\(r.source))"
        whenReady(deadline: Date().addingTimeInterval(900), what: what) { [weak self] ok in
            guard let self = self, !self.quitting else { return }
            if ok { self.runAddBatch(r); return }
            Log.write("\(what): the unit is not ready, \(r.urls.count) path(s) not added")
            if r.autoAnswer == nil {
                self.sheet("The unit is not ready, nothing was added to the card.",
                           "\(r.urls.count) path(s) dropped. Try again once the panel shows the unit running.") { _ in self.endBatch() }
            } else {
                self.endBatch()
            }
        }
    }

    func endBatch() {
        adding = false
        drainPendingAdds()
    }

    /// The batch itself, the unit ready: folders expanded, each file GET
    /// /samples/add?path=<abs>, then the alert, Re-insert being GET
    /// /samples/commit.
    func runAddBatch(_ req: AddRequest) {
        let source = req.source
        let (files, skipped) = Self.sampleFiles(req.urls)
        for u in skipped { Log.write("add (\(source)): skipped, not an audio file: \(u.path)") }
        Log.write("add (\(source)): \(files.count) file(s)")
        var added: [String] = []      // lines for the alert: new on the card
        var already: [String] = []    // identical bytes already on the card
        var failed: [String] = []
        var i = 0
        func next() {
            guard i < files.count else { finish(); return }
            let u = files[i]; i += 1
            // conversion (afconvert of a long flac) can take a while: a generous timeout
            server.get("/samples/add", query: [("path", u.path)], timeout: 300) { ok, r, why in
                if ok, let r = r, r["ok"] as? Bool == true {
                    let name = r["name"] as? String ?? u.lastPathComponent
                    let conv = r["converted"] as? Bool ?? false
                    let note = r["note"] as? String ?? ""
                    var line = u.lastPathComponent
                    if name != u.lastPathComponent { line += " -> " + name }
                    if conv { line += " (converted to 16-bit 44.1 kHz WAV)" }
                    if !note.isEmpty { line += conv ? "; " + note : " (" + note + ")" }
                    // the reply's `pending` lists what the next re-insert would add;
                    // identical bytes already on the card are not in it (12 Sep 2026)
                    if let pend = r["pending"] as? [String], !pend.contains(name) {
                        already.append(line)
                    } else {
                        added.append(line)
                    }
                    Log.write("add ok: \(u.path) -> \(name) converted=\(conv)\(note.isEmpty ? "" : " note=\(note)")")
                } else {
                    let e = (r?["error"] as? String) ?? why
                    failed.append("\(u.lastPathComponent): \(e)")
                    Log.write("add failed: \(u.path): \(e)")
                }
                next()
            }
        }
        func finish() {
            let n = added.count
            var details: [String] = added
            details += already.map { "already on the card: " + $0 }
            details += failed.map { "failed: " + $0 }
            details += skipped.map { "skipped, not an audio file: " + $0.lastPathComponent }
            // An NSAlert grows with its text and a folder drop can hold dozens
            // of files (measured 12 Sep 2026: 30 lines put the buttons off
            // screen), so the sheet shows a few and the log keeps the rest.
            let shown = 8
            if details.count > shown {
                let rest = details.count - shown
                details = Array(details.prefix(shown)) + ["... and \(rest) more (see \(Log.path))"]
            }
            let summary = "\(n) added, \(already.count) already on the card, \(failed.count) failed, \(skipped.count) skipped"
            if let ans = req.autoAnswer {
                Log.write("add batch done: \(summary); auto-answer \(ans)")
                if ans == "commit" && n > 0 { commit() } else { endBatch() }
                return
            }
            Log.write("add batch done: \(summary)")
            if n > 0 {
                sheet("\(n) file\(n == 1 ? "" : "s") added to the card.",
                      "Re-insert the card now? (the unit reboots, ~40 s)"
                      + (details.isEmpty ? "" : "\n\n" + details.joined(separator: "\n")),
                      buttons: ["Re-insert", "Later"]) { [weak self] b in
                    if b == 0 { self?.commit() } else { Log.write("add batch: later (no re-insert)"); self?.endBatch() }
                }
            } else if !already.isEmpty && failed.isEmpty {
                sheet("Already on the card.",
                      "Nothing new to add, so no re-insert is needed.\n\n" + details.joined(separator: "\n")) { [weak self] _ in
                    Log.write("add batch: nothing new")
                    self?.endBatch()
                }
            } else {
                sheet("No files were added to the card.",
                      details.isEmpty ? "Nothing to add." : details.joined(separator: "\n")) { [weak self] _ in
                    Log.write("add batch: nothing added")
                    self?.endBatch()
                }
            }
        }
        next()
    }

    /// GET /samples/commit: the card rebuilt from the pool and the unit
    /// rebooted on it (the page shows the phase). The batch ends with the
    /// reply, or with the failure sheet; a queued batch then waits for
    /// ready again, i.e. for the reboot.
    func commit() {
        server.get("/samples/commit", timeout: 30) { [weak self] ok, r, why in
            guard let self = self else { return }
            if ok, let r = r, r["ok"] as? Bool == true {
                Log.write("commit: \(r["phase"] as? String ?? "ok")")
                self.endBatch()
            } else {
                let e = (r?["error"] as? String) ?? why
                Log.write("commit failed: \(e)")
                self.sheet("The card could not be re-inserted", e + "\n\nThe files stay in the pool: RE-INSERT CARD on the panel page, or the next batch.") { _ in self.endBatch() }
            }
        }
    }

    /// GET /samples "pool" -> the AUDIO folder the card is built from, opened
    /// in Finder (a file put there by hand is on the card after the next
    /// /samples/commit).
    @objc func showCardAudioFolder(_ sender: Any?) {
        server.get("/samples", timeout: 10) { [weak self] ok, r, why in
            guard let self = self else { return }
            if ok, let pool = r?["pool"] as? String, !pool.isEmpty {
                Log.write("show pool: \(pool)")
                NSWorkspace.shared.open(URL(fileURLWithPath: pool, isDirectory: true))
            } else {
                let e = (r?["error"] as? String) ?? (why.isEmpty ? "no pool in the reply" : why)
                Log.write("show pool failed: \(e)")
                self.sheet("The card's audio folder is not known", "GET /samples on port \(self.server.port): \(e)")
            }
        }
    }

    // MARK: audio

    /// /status every 2 s (a local GET that the server answers without an
    /// emulator action), so the Sound checkbox follows the server: on/off
    /// from "sound", enabled only at phase ready (a switch is a reboot, and
    /// the server refuses one while it boots). Started at launch: before the
    /// server answers the probe fails within milliseconds and the item stays
    /// disabled. Changes are logged.
    func startStatusPoll() {
        statusPoll?.invalidate()
        statusPoll = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in self?.pollStatusOnce() }
        pollStatusOnce()
    }

    func pollStatusOnce() {
        guard !statusProbing, !quitting else { return }
        statusProbing = true
        server.probe(timeout: 1.5) { [weak self] ok, st in
            guard let self = self else { return }
            self.statusProbing = false
            self.noteStatus(ok ? st : nil)
        }
    }

    func noteStatus(_ st: [String: Any]?) {
        let sound = st?["sound"] as? Bool
        let ph = st.map { $0["phase"] as? String ?? "?" } ?? "no answer"
        soundNote = st?["sound_note"] as? String ?? ""
        if sound != soundOn || ph != phase {
            Log.write("status: phase \(ph), sound \(sound.map { $0 ? "on" : "off" } ?? "unknown")"
                      + (soundNote.isEmpty ? "" : " (\(soundNote))"))
        }
        // the output device: re-sent whenever the unit returns to ready (a
        // fresh server knows nothing; the running one answers "already on")
        if ph == "ready" && phase != "ready" {
            if let w = outputWanted {
                applyOutput(w, source: readyBefore ? "ready again after \(phase)" : "ready")
            }
            readyBefore = true
        }
        soundOn = sound
        phase = ph
        // the card (O19)
        let ejected = st?["card_ejected"] as? Bool ?? false
        let mount = st?["card_mount"] as? String
        let persistent = (st?["card_mode"] as? String) == "persistent"
        let rw = st?["card_rw"] as? Bool
        if ejected != cardEjected || mount != cardMount || persistent != cardPersistent || rw != cardRw {
            Log.write("status: card \(st?["card"] as? String ?? "-") \(persistent ? "persistent" : "scratch"), write-back \(rw.map { $0 ? "on" : "off" } ?? "?")"
                      + (ejected ? ", EJECTED at \(mount ?? "?")" : ""))
        }
        cardEjected = ejected; cardMount = mount; cardPersistent = persistent; cardRw = rw
        cardPathFromStatus = st?["card"] as? String
        updateSoundItem()
        updateCardItems()
    }

    func updateSoundItem() {
        guard let item = soundItem else { return }
        let state: NSControl.StateValue = soundOn == true ? .on : .off
        let enabled = soundOn != nil && phase == "ready" && !quitting
        if state != item.state || enabled != item.isEnabled {
            Log.write("Sound checkbox: \(state == .on ? "checked" : "unchecked"), \(enabled ? "enabled" : "disabled")")
        }
        item.state = state
        item.isEnabled = enabled
    }

    /// A menu is about to open: a fresh /status for the checkbox and the card
    /// items; the Audio menu and the Output Device submenu also refresh the
    /// device list (and the checkmark) from /audio/devices.
    func menuWillOpen(_ menu: NSMenu) {
        pollStatusOnce()
        if menu === audioMenu || menu === outputMenu { refreshOutputMenu() }
    }

    // MARK: output device

    /// The device to (re)apply: VIRTUAL_PANEL_OUTPUT for this launch, else
    /// the remembered choice; nil = nothing chosen (the server's default: off).
    var outputWanted: String? {
        if let l = launchOutput { return l }
        if let d = UserDefaults.standard.string(forKey: Self.outputKey), !d.isEmpty { return d }
        return nil
    }

    /// The map a device with `channels` outputs gets (the server's OUTPUT_ORDER
    /// for the `tracks` capture: main, cue, tracks 1-8, ESAI words 0/1, 6/7).
    static func mapFor(channels: Int) -> String {
        let pairs = channels / 2
        var parts: [String] = ["main L/R > 1-2"]
        if pairs >= 2 { parts.append("cue L/R > 3-4") }
        if pairs >= 3 {
            let tracks = min(8, pairs - 2)
            parts.append(tracks == 1 ? "track 1 > 5-6" : "tracks 1-\(tracks) > 5-\(4 + 2 * tracks)")
        }
        if pairs >= 11 { parts.append("ESAI words 0/1 > 21-22") }
        if pairs >= 12 { parts.append("6/7 > 23-24") }
        return parts.joined(separator: ", ")
    }

    /// The channel-map line under the submenu.
    static func mapLine(_ out: [String: Any]?) -> String {
        if let o = out, o["running"] as? Bool == true, let name = o["device"] as? String {
            let map = (o["map"] as? [String]) ?? []
            let ch = o["channels"] as? Int ?? 0
            return "\(name): " + (map.isEmpty ? "\(ch) channels" : map.joined(separator: ", "))
        }
        var line = "Output off -- main L/R > 1-2, cue L/R > 3-4, tracks 1-8 > 5-20, ESAI words 0/1 > 21-22, 6/7 > 23-24"
        if let n = out?["note"] as? String, !n.isEmpty { line += " (\(n))" }
        return line
    }

    /// GET /audio/devices -- the list, the output's state and the capture
    /// mode in one reply -- then the submenu rebuilt.
    func refreshOutputMenu() {
        guard !outputRefreshing, !quitting else { return }
        outputRefreshing = true
        server.get("/audio/devices", timeout: 5) { [weak self] ok, r, why in
            guard let self = self else { return }
            self.outputRefreshing = false
            if ok, let r = r {
                self.outputDevices = (r["devices"] as? [[String: Any]]) ?? []
                self.outputState = r["output"] as? [String: Any]
                if r["ok"] as? Bool != true, let e = r["error"] as? String { Log.write("output devices: \(e)") }
            } else {
                Log.write("output devices: \(why)")
                self.outputDevices = []
                self.outputState = nil
            }
            self.rebuildOutputMenu()
        }
    }

    /// Off + each device (channels, "default"), the checkmark on the running
    /// one; the map line follows the state.
    func rebuildOutputMenu() {
        guard let menu = outputMenu else { return }
        let active = outputState?["running"] as? Bool == true ? outputState?["device"] as? String : nil
        menu.removeAllItems()
        let off = menu.addItem(withTitle: "Off", action: #selector(chooseOutput(_:)), keyEquivalent: "")
        off.target = self
        off.representedObject = "off"
        off.state = active == nil ? .on : .off
        if !outputDevices.isEmpty { menu.addItem(.separator()) }
        for d in outputDevices {
            guard let name = d["name"] as? String else { continue }
            let ch = d["channels"] as? Int ?? 0
            let isDefault = d["default"] as? Bool ?? false
            let item = menu.addItem(withTitle: "\(name) (\(ch) ch\(isDefault ? ", default" : ""))",
                                    action: #selector(chooseOutput(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = name
            item.state = active == name ? .on : .off
            item.toolTip = Self.mapFor(channels: ch)
        }
        outputMapItem?.title = Self.mapLine(outputState)
    }

    /// A pick in the submenu: remembered (Off forgets), sent to the server.
    @objc func chooseOutput(_ sender: NSMenuItem) {
        let name = (sender.representedObject as? String) ?? "off"
        launchOutput = nil     // a choice made here outranks the hook for the rest of the launch
        if name == "off" { UserDefaults.standard.removeObject(forKey: Self.outputKey) }
        else { UserDefaults.standard.set(name, forKey: Self.outputKey) }
        Log.write("output (menu): \(name)\(name == "off" ? "" : " (remembered)")")
        applyOutput(name, source: "menu")
    }

    /// GET /audio/output?device=<name|off>: the reply logged (device,
    /// channels, latency, map), the menu state refreshed; a refusal is
    /// logged and, from the menu, shown as a sheet.
    func applyOutput(_ name: String, source: String) {
        Log.write("output (\(source)): GET /audio/output?device=\(name)")
        server.get("/audio/output", query: [("device", name)], timeout: 15) { [weak self] ok, r, why in
            guard let self = self else { return }
            let out = r?["output"] as? [String: Any]
            if ok, let r = r, r["ok"] as? Bool == true {
                let note = r["note"] as? String ?? ""
                if let o = out, o["running"] as? Bool == true {
                    let lat = o["latency_ms"].map { "\($0)" } ?? "?"
                    let map = ((o["map"] as? [String]) ?? []).joined(separator: ", ")
                    Log.write("output (\(source)): \(o["device"] as? String ?? "?") running, \(o["channels"] as? Int ?? 0) channels, latency \(lat) ms, \(map) -- \(note)")
                } else {
                    Log.write("output (\(source)): \(note)")
                }
            } else {
                let e = (r?["error"] as? String) ?? why
                Log.write("output (\(source)): refused: \(e)")
                if source == "menu" { self.sheet("The output device was not switched", e) }
            }
            self.outputState = out
            self.rebuildOutputMenu()
        }
    }

    static func takeName(_ n: Int) -> String { String(format: "octatrack-take-%03d.wav", n) }

    /// /audio/status -> the latest take (highest n) and whether it is the
    /// one still recording.
    static func latestTake(_ r: [String: Any]) -> (n: Int, recording: Bool)? {
        let takes = (r["takes"] as? [[String: Any]]) ?? []
        guard let n = takes.compactMap({ $0["n"] as? Int }).max() else { return nil }
        let cur = r["take"] as? [String: Any]
        return (n, cur?["n"] as? Int == n && (cur?["recording"] as? Bool ?? false))
    }

    /// What Save Main Out Recording... saves, from /audio/status: the latest
    /// take, else the ring (the last 180 s of the main output) when it holds
    /// anything, else nil with the reason.
    func recording(in r: [String: Any]) -> (url: URL, name: String, what: String)? {
        if let t = Self.latestTake(r) {
            return (server.audioURL(take: t.n), Self.takeName(t.n), "take \(t.n)" + (t.recording ? " (still recording)" : ""))
        }
        let first = r["first"] as? Int ?? 0, end = r["end"] as? Int ?? 0
        if end > first {
            return (server.audioURL(from: first, to: end), "octatrack-main-out.wav", "the ring, frames \(first)..\(end)")
        }
        return nil
    }

    func nothingToSave(_ r: [String: Any]) -> String {
        if r["sound"] as? Bool == false {
            return "Sound is off (\(r["note"] as? String ?? "DSP cores not running")): Audio > Sound turns it on (the unit reboots, ~1 min)."
        }
        return "Nothing has been captured yet. Press PLAY on the panel: the unit's main output is recorded until STOP, and that is the take."
    }

    /// GET /audio/status, then the latest take (or the ring) through the save
    /// flow; a sheet says why when there is nothing, or the server has no
    /// audio endpoints (HTTP 404).
    @objc func saveMainOut(_ sender: Any?) {
        server.get("/audio/status", timeout: 10) { [weak self] ok, r, why in
            guard let self = self else { return }
            guard ok, let r = r else {
                Log.write("save (menu): /audio/status: \(why)")
                self.sheet("The recording is not available",
                           "GET /audio/status on port \(self.server.port): \(why)"
                           + (why.hasPrefix("HTTP 404") ? "\n\nThis server has no audio endpoints (an older panel_server.py?)." : ""))
                return
            }
            guard let rec = self.recording(in: r) else {
                Log.write("save (menu): nothing to save")
                self.sheet("Nothing to save", self.nothingToSave(r))
                return
            }
            Log.write("save (menu): \(rec.what)")
            self.saveRecording(rec.url, suggested: rec.name, source: "menu")
        }
    }

    /// The save flow, whatever asked for it (the menu, a SAVE link in the
    /// page, VIRTUAL_PANEL_SAVE): a save panel as a sheet with the suggested
    /// name (the contract's take name, or the reply's Content-Disposition
    /// for an intercepted link), remembering the folder, then the download
    /// to the chosen file. VIRTUAL_PANEL_SAVE_DIR skips the panel.
    func saveRecording(_ url: URL, suggested: String, source: String) {
        guard !quitting else { return }
        Log.write("save (\(source)): \(url.absoluteString) as \(suggested)")
        if let dir = saveDir {
            download(url, to: dir.appendingPathComponent(suggested), source: source)
            return
        }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggested
        panel.allowedContentTypes = [.wav]
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        panel.prompt = "Save"
        panel.message = "The unit's main output as the server captured it (16-bit stereo 44.1 kHz WAV)"
        if let d = UserDefaults.standard.string(forKey: Self.saveDirKey), FileManager.default.fileExists(atPath: d) {
            panel.directoryURL = URL(fileURLWithPath: d, isDirectory: true)
        }
        panel.beginSheetModal(for: window) { [weak self] resp in
            guard let self = self, !self.quitting else { return }
            guard resp == .OK, let dest = panel.url else { Log.write("save (\(source)): cancelled"); return }
            UserDefaults.standard.set(dest.deletingLastPathComponent().path, forKey: Self.saveDirKey)
            self.download(url, to: dest, source: source)
        }
    }

    /// GET url -> dest; logged, a failure also in a sheet (the server's own
    /// error for a missing take: "HTTP 404: no take 9").
    func download(_ url: URL, to dest: URL, source: String, _ done: ((Bool) -> Void)? = nil) {
        server.download(url, to: dest, timeout: 300) { [weak self] ok, bytes, why, name in
            guard let self = self else { return }
            if ok {
                Log.write("saved (\(source)): \(dest.path) \(bytes) B (the server named it \(name))")
            } else {
                Log.write("save failed (\(source)): \(url.absoluteString) -> \(dest.path): \(why)")
                self.sheet("The recording could not be saved", url.absoluteString + "\n\n" + why)
            }
            done?(ok)
        }
    }

    /// Audio > Show Takes Folder: the folder of the takes /audio/status lists
    /// (out/_panel_takes_<port>/ in the repo), in Finder.
    @objc func showTakesFolder(_ sender: Any?) {
        server.get("/audio/status", timeout: 10) { [weak self] ok, r, why in
            guard let self = self else { return }
            let conventional = self.repo.appendingPathComponent("out/_panel_takes_\(self.server.port)")
            var dir: URL? = nil
            if ok, let r = r {
                let files = ((r["takes"] as? [[String: Any]]) ?? []).compactMap { $0["file"] as? String }
                if let f = files.last ?? (r["take"] as? [String: Any])?["file"] as? String {
                    dir = URL(fileURLWithPath: f).deletingLastPathComponent()
                }
            }
            if dir == nil, FileManager.default.fileExists(atPath: conventional.path) { dir = conventional }
            guard let d = dir else {
                let e = ok ? "No take yet: press PLAY on the panel, STOP closes the take."
                           : "GET /audio/status on port \(self.server.port): \(why)"
                Log.write("show takes: \(e)")
                self.sheet("No takes folder yet", e + "\n\nTakes are written to \(conventional.path).")
                return
            }
            Log.write("show takes: \(d.path)")
            NSWorkspace.shared.open(d)
        }
    }

    /// Audio > Sound: the checkbox as it is now flipped, after a sheet that
    /// says the unit reboots.
    @objc func toggleSound(_ sender: Any?) {
        guard let cur = soundOn else { return }
        let on = !cur
        sheet(on ? "Switch sound on?" : "Switch sound off?",
              on ? "The unit reboots with the DSP cores running (~1 min). Its main output is then captured: the page's headphones monitor it and every PLAY..STOP is a take. While it plays the unit runs ~9x slower than real time (~3x slower than without the cores)."
                 : "The unit reboots without the DSP cores (~40 s): faster, but silent. The takes so far are kept.",
              buttons: [on ? "Switch On" : "Switch Off", "Cancel"]) { [weak self] b in
            guard b == 0 else { Log.write("sound (menu): cancelled"); return }
            self?.setSound(on, source: "menu")
        }
    }

    /// GET /audio/enable?on=1|0: the server reboots its port child with or
    /// without --dsp; /status "phase" shows it and the poll re-enables the
    /// checkbox at ready. ok:false (already so, busy, route A) is logged and,
    /// unless `quiet` (the hook), shown.
    func setSound(_ on: Bool, source: String, quiet: Bool = false) {
        Log.write("sound (\(source)): GET /audio/enable?on=\(on ? 1 : 0)")
        soundItem.isEnabled = false
        server.get("/audio/enable", query: [("on", on ? "1" : "0")], timeout: 30) { [weak self] ok, r, why in
            guard let self = self else { return }
            if ok, let r = r, r["ok"] as? Bool == true {
                Log.write("sound (\(source)): \(r["phase"] as? String ?? "ok")")
            } else {
                let e = (r?["note"] as? String) ?? (r?["error"] as? String) ?? why
                Log.write("sound (\(source)): refused: \(e)")
                if !quiet { self.sheet("Sound was not switched", e) }
            }
            self.statusProbing = false   // a poll in flight would drop this fresh one
            self.pollStatusOnce()
        }
    }

    /// VIRTUAL_PANEL_SAVE=<path>: once ready, poll /audio/status every second
    /// until a take exists and none is recording (logged as that changes),
    /// then save the latest take to <path> without a panel; at the deadline
    /// save what there is (the latest take, else the ring), or nothing.
    func saveHook(_ dest: URL, wait: TimeInterval) {
        whenReady(deadline: Date().addingTimeInterval(900), what: "VIRTUAL_PANEL_SAVE") { [weak self] ok in
            guard let self = self, !self.quitting else { return }
            guard ok else { Log.write("VIRTUAL_PANEL_SAVE: the unit is not ready, nothing saved"); return }
            self.waitForTake(deadline: Date().addingTimeInterval(wait), seen: "") { r in
                guard let r = r, let rec = self.recording(in: r) else {
                    Log.write("VIRTUAL_PANEL_SAVE: nothing to save")
                    return
                }
                Log.write("VIRTUAL_PANEL_SAVE: saving \(rec.what)")
                self.download(rec.url, to: dest, source: "VIRTUAL_PANEL_SAVE")
            }
        }
    }

    func waitForTake(deadline: Date, seen: String, _ done: @escaping ([String: Any]?) -> Void) {
        server.get("/audio/status", timeout: 5) { [weak self] ok, r, why in
            guard let self = self, !self.quitting else { return }
            let take = ok ? r.flatMap(Self.latestTake) : nil
            let state = !ok ? "no /audio/status: \(why)"
                            : take.map { "take \($0.n)\($0.recording ? ", recording" : "")" } ?? "no take yet"
            if state != seen { Log.write("VIRTUAL_PANEL_SAVE: waiting for a take (now: \(state))") }
            if let t = take, !t.recording { done(r); return }
            if Date() > deadline {
                Log.write("VIRTUAL_PANEL_SAVE: deadline (\(state)), saving what there is")
                done(ok ? r : nil)
                return
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
                self.waitForTake(deadline: deadline, seen: state, done)
            }
        }
    }

    /// VIRTUAL_PANEL_NAV=open:<path> | go:<path> (a bare value is open:):
    /// once the page has loaded, window.open(<path>) / location.href =
    /// <path> from inside it -- what a SAVE link (target=_blank) or a script
    /// does -- with a marker set on the page first; 4 s later the marker,
    /// location.href and the title are read back and logged: "page kept"
    /// when the interception left the page in place.
    func fireNavHook() {
        let spec = navHook
        navHook = ""
        let go = spec.hasPrefix("go:")
        let path = go ? String(spec.dropFirst(3)) : (spec.hasPrefix("open:") ? String(spec.dropFirst(5)) : spec)
        navHookMarker = "vp-\(getpid())-\(Int(Date().timeIntervalSince1970))"
        let js = "window.__vpNavMarker = '\(navHookMarker)'; "
               + (go ? "location.href = '\(path)';" : "window.open('\(path)');") + " 'fired'"
        Log.write("VIRTUAL_PANEL_NAV: \(go ? "location.href =" : "window.open") \(path)")
        web.evaluateJavaScript(js) { r, e in
            Log.write("VIRTUAL_PANEL_NAV: js -> \(r.map { "\($0)" } ?? "nil")\(e.map { ", error: \($0.localizedDescription)" } ?? "")")
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 4.0) { [weak self] in
            guard let self = self, !self.quitting else { return }
            self.web.evaluateJavaScript("[String(window.__vpNavMarker), location.href, document.title].join(' | ')") { r, e in
                let s = (r as? String) ?? "error: \(e?.localizedDescription ?? "?")"
                let kept = s.hasPrefix(self.navHookMarker + " | ")
                Log.write("VIRTUAL_PANEL_NAV: \(kept ? "page kept" : "PAGE NAVIGATED AWAY") (\(s)); intercepted \(self.interceptedNavs)")
            }
        }
    }

    // MARK: web view

    func webView(_ w: WKWebView, didFinish nav: WKNavigation!) {
        if let u = w.url, u.scheme == "http" {
            Log.write("loaded \(u)")
            if !navHook.isEmpty { fireNavHook() }
        }
    }

    /// A navigation whose reply is audio/wav -- the page's SAVE links
    /// (/audio.wav?take=N, target=_blank, brought here by createWebViewWith
    /// below) or a script's location change -- is cancelled and becomes the
    /// save flow, the name from the reply's Content-Disposition; the page
    /// stays (WebKit reports the cancelled load as WebKitErrorDomain 102,
    /// ignored in didFailProvisionalNavigation). A /audio.wav reply that is
    /// not audio (the 404 JSON for a missing take) is cancelled too and its
    /// error shown, instead of the JSON replacing the panel.
    func webView(_ w: WKWebView, decidePolicyFor r: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let mime = (r.response.mimeType ?? "").lowercased()
        let u = r.response.url
        let isWav = ["audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"].contains(mime)
        if isWav, let u = u {
            interceptedNavs += 1
            decisionHandler(.cancel)
            Log.write("intercepted an audio/wav navigation: \(u.absoluteString)")
            saveRecording(u, suggested: r.response.suggestedFilename ?? "octatrack-main-out.wav", source: "page link")
            return
        }
        if let u = u, u.path == "/audio.wav" {
            interceptedNavs += 1
            decisionHandler(.cancel)
            let code = (r.response as? HTTPURLResponse)?.statusCode ?? 0
            Log.write("intercepted a /audio.wav navigation that is not audio (\(mime), HTTP \(code)): \(u.absoluteString)")
            server.get(u.path + (u.query.map { "?" + $0 } ?? ""), timeout: 10) { [weak self] _, j, why in
                let e = (j?["error"] as? String) ?? why
                Log.write("recording not available: \(e)")
                self?.sheet("The recording is not available", u.absoluteString + "\n\n" + e)
            }
            return
        }
        decisionHandler(.allow)
    }

    /// target=_blank links and window.open() land here; without a UI
    /// delegate WebKit drops them. There is one window, so the request is
    /// loaded in it: an audio/wav reply is intercepted above (the page
    /// stays), anything else replaces the page as a plain link would.
    func webView(_ w: WKWebView, createWebViewWith cfg: WKWebViewConfiguration, for a: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if a.targetFrame == nil, let u = a.request.url {
            Log.write("new-window navigation loaded in the panel's view: \(u.absoluteString)")
            w.load(a.request)
        }
        return nil
    }

    /// JS alert()/confirm() as sheets (WebKit shows nothing and answers false
    /// without these). quit() ends an open one as Cancel; the handler is
    /// always called, WebKit insists.
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage msg: String, initiatedByFrame f: WKFrameInfo,
                 completionHandler: @escaping () -> Void) {
        guard !quitting else { completionHandler(); return }
        let a = NSAlert()
        a.messageText = msg
        a.addButton(withTitle: "OK")
        a.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage msg: String, initiatedByFrame f: WKFrameInfo,
                 completionHandler: @escaping (Bool) -> Void) {
        guard !quitting else { completionHandler(false); return }
        let a = NSAlert()
        a.messageText = msg
        a.addButton(withTitle: "OK")
        a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn) }
    }

    /// The server went away between /status and the page (or was killed):
    /// back to the placeholder and keep polling. Not spawning here on
    /// purpose: an attached server may be the user's own being restarted
    /// (the Open Project alert tells them to), and a second bind on the port
    /// would fail; Reload is the explicit "replace it". -999 is our own
    /// loadHTMLString cancelling an in-flight load, not a failure; and
    /// WebKitErrorDomain 102 (frame load interrupted by policy change) is a
    /// navigation the response policy above cancelled -- an intercepted
    /// save -- after which the page is still there.
    func webView(_ w: WKWebView, didFailProvisionalNavigation nav: WKNavigation!, withError e: Error) {
        let ns = e as NSError
        if ns.code == NSURLErrorCancelled { return }
        if ns.domain == "WebKitErrorDomain" && ns.code == 102 {
            Log.write("navigation interrupted by the response policy (an intercepted save); the page stays: \(w.url?.absoluteString ?? "?")")
            return
        }
        Log.write("navigation failed: \(ns.domain) \(ns.code): \(e.localizedDescription)")
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
