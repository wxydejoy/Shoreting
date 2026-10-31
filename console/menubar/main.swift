import Cocoa

private let repo = URL(fileURLWithPath: "/Users/weiekko/dock", isDirectory: true)
private let consoleDir = repo.appendingPathComponent("console", isDirectory: true)
private let port = 17880
private let logURL = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Logs/岸亭控制台.log")
private let serviceOrder = ["hub", "mini", "tts"]

private func consoleURL(_ path: String) -> URL {
    URL(string: "http://127.0.0.1:\(port)\(path)")!
}

final class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let status = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    var server: Process?
    var serviceItems: [String: NSMenuItem] = [:]

    func applicationDidFinishLaunching(_ notification: Notification) {
        status.button?.image = menuIcon()
        let menu = NSMenu()
        menu.delegate = self

        let open = item("打开控制台", action: #selector(openConsole), key: "o")
        menu.addItem(open)
        menu.addItem(item("功能开关", action: #selector(openFeatures), key: "f"))
        menu.addItem(.separator())

        for id in serviceOrder {
            let parent = NSMenuItem(title: id, action: nil, keyEquivalent: "")
            let sub = NSMenu()
            for (action, title) in [("start", "启动"), ("stop", "关闭"), ("restart", "重启")] {
                let child = item(title, action: #selector(runAction(_:)), key: "")
                child.representedObject = "\(id)/\(action)"
                sub.addItem(child)
            }
            parent.submenu = sub
            serviceItems[id] = parent
            menu.addItem(parent)
        }

        menu.addItem(.separator())
        menu.addItem(item("退出", action: #selector(quit), key: "q"))
        status.menu = menu
        startServerIfNeeded()
    }

    func menuWillOpen(_ menu: NSMenu) {
        let rows = fetchStatus()
        for row in rows {
            guard let id = row["id"] as? String, let item = serviceItems[id] else { continue }
            let name = (row["name"] as? String) ?? id
            let state = (row["state_label"] as? String) ?? ""
            item.title = state.isEmpty ? name : "\(name) · \(state)"
        }
    }

    @objc func openConsole() {
        NSWorkspace.shared.open(consoleURL("/"))
    }

    @objc func openFeatures() {
        NSWorkspace.shared.open(consoleURL("/features"))
    }

    @objc func runAction(_ sender: NSMenuItem) {
        guard let path = sender.representedObject as? String else { return }
        var request = URLRequest(url: consoleURL("/api/services/\(path)"))
        request.httpMethod = "POST"
        URLSession.shared.dataTask(with: request).resume()
    }

    @objc func quit() {
        server?.terminate()
        NSApp.terminate(nil)
    }

    // Cmd+Q、注销、系统关掉 app 时也要带走服务端，别留个孤儿占着 17880
    func applicationWillTerminate(_ notification: Notification) {
        server?.terminate()
    }

    private func item(_ title: String, action: Selector, key: String) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: action, keyEquivalent: key)
        entry.target = self
        return entry
    }

    private func startServerIfNeeded() {
        if consoleResponds() { return }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = ["python3", "-m", "dock_console", "--no-browser", "--port", "\(port)"]
        proc.currentDirectoryURL = consoleDir
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = consoleDir.path
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"] {
            env.removeValue(forKey: key)
        }
        proc.environment = env
        let log = FileHandle(forWritingAtPath: logURL.path) ?? {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
            return FileHandle(forWritingAtPath: logURL.path)!
        }()
        log.seekToEndOfFile()
        proc.standardOutput = log
        proc.standardError = log
        do {
            try proc.run()
            server = proc
        } catch {
            NSLog("岸亭控制台没启动：\(error.localizedDescription)")
        }
    }

    private func consoleResponds() -> Bool {
        var request = URLRequest(url: consoleURL("/api/status"))
        request.timeoutInterval = 0.4
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: request) { _, response, _ in
            ok = (response as? HTTPURLResponse)?.statusCode == 200
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 0.5)
        return ok
    }

    private func fetchStatus() -> [[String: Any]] {
        var request = URLRequest(url: consoleURL("/api/status"))
        request.timeoutInterval = 1.2
        let sem = DispatchSemaphore(value: 0)
        var rows: [[String: Any]] = []
        URLSession.shared.dataTask(with: request) { data, _, _ in
            defer { sem.signal() }
            guard let data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let services = json["services"] as? [[String: Any]] else { return }
            rows = services
        }.resume()
        _ = sem.wait(timeout: .now() + 1.4)
        return rows
    }
}

private func menuIcon() -> NSImage {
    let size = NSSize(width: 18, height: 18)
    let image = NSImage(size: size, flipped: false) { _ in
        NSColor.black.setStroke()
        NSColor.black.setFill()
        let rim = NSBezierPath(ovalIn: NSRect(x: 4.2, y: 4.2, width: 9.6, height: 9.6))
        rim.lineWidth = 1.4
        rim.stroke()
        let hub = NSBezierPath(ovalIn: NSRect(x: 7.6, y: 7.6, width: 2.8, height: 2.8))
        hub.lineWidth = 1.1
        hub.stroke()
        for i in 0..<4 {
            let a = CGFloat(i) * .pi / 4
            let c = CGPoint(x: 9, y: 9)
            let inner = CGPoint(x: c.x + cos(a) * 4.6, y: c.y + sin(a) * 4.6)
            let outer = CGPoint(x: c.x + cos(a) * 8.1, y: c.y + sin(a) * 8.1)
            let spoke = NSBezierPath()
            spoke.move(to: inner)
            spoke.line(to: outer)
            spoke.lineWidth = 1.35
            spoke.stroke()
            let knob = NSBezierPath(ovalIn: NSRect(x: outer.x - 1.15, y: outer.y - 1.15, width: 2.3, height: 2.3))
            knob.fill()
        }
        return true
    }
    image.isTemplate = true
    return image
}

@main
struct ShoretingMain {
    static func main() {
        let app = NSApplication.shared
        let delegate = App()
        app.setActivationPolicy(.accessory)
        app.delegate = delegate
        app.run()
    }
}
