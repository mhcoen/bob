// cgwindowid - Get the CGWindowNumber for a named app's window.
// Usage: cgwindowid "AppName" ["WindowTitle"]
// Prints the numeric window ID to stdout, exits 1 if not found.

import CoreGraphics
import Foundation

guard CommandLine.arguments.count >= 2 else {
    fputs("Usage: cgwindowid AppName [WindowTitle]\n", stderr)
    exit(1)
}

let appName = CommandLine.arguments[1]
let windowTitle: String? = CommandLine.arguments.count >= 3
    ? CommandLine.arguments[2]
    : nil

let opts = CGWindowListOption(
    arrayLiteral: .optionOnScreenOnly, .excludeDesktopElements
)
guard let windows = CGWindowListCopyWindowInfo(opts, kCGNullWindowID)
    as? [[String: Any]] else {
    fputs("Error: Cannot query window list\n", stderr)
    exit(1)
}

for win in windows {
    guard let owner = win[kCGWindowOwnerName as String] as? String,
          owner == appName else { continue }

    if let title = windowTitle {
        let winTitle = win[kCGWindowName as String] as? String ?? ""
        if winTitle != title { continue }
    }

    if let winID = win[kCGWindowNumber as String] as? Int {
        print(winID)
        exit(0)
    }
}

fputs("Error: No window found for '\(appName)'\n", stderr)
exit(1)
