import Foundation

enum TailnetCandidateFilter {
    private static func isTailnetAddress(_ address: String) -> Bool {
        let value = address.split(separator: "%", maxSplits: 1).first.map(String.init)?.lowercased() ?? ""
        if value.hasPrefix("fd7a:115c:a1e0:") { return true }
        let fields = value.split(separator: ".", omittingEmptySubsequences: false)
        guard fields.count == 4 else { return false }
        let parts = fields.compactMap { Int($0) }
        return parts.count == 4 && parts.allSatisfy { (0...255).contains($0) }
            && parts[0] == 100 && (64...127).contains(parts[1])
    }

    static func isTailnetHost(_ host: String?) -> Bool {
        guard let host else { return false }
        let name = host.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        return name.hasSuffix(".ts.net") || isTailnetAddress(name)
    }

    static func filter(_ offer: String) -> (sdp: String, changed: Bool) {
        let lines = offer.components(separatedBy: "\r\n")
        var sections: [[String]] = []
        for line in lines {
            if sections.isEmpty || line.hasPrefix("m=") { sections.append([]) }
            sections[sections.count - 1].append(line)
        }
        var changed = false
        for index in sections.indices {
            let candidates = sections[index].filter { $0.hasPrefix("a=candidate:") }
            let selected = candidates.filter { line in
                let fields = line.split(whereSeparator: { $0.isWhitespace })
                return fields.count >= 8 && isTailnetAddress(String(fields[4]))
            }
            guard !selected.isEmpty else { continue }
            let selectedSet = Set(selected)
            if selected.count < candidates.count {
                changed = true
                sections[index].removeAll { $0.hasPrefix("a=candidate:") && !selectedSet.contains($0) }
            }
        }
        return (sections.flatMap { $0 }.joined(separator: "\r\n"), changed)
    }
}
