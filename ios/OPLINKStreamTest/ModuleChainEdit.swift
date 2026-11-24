import Foundation

enum ModuleChainEdit {
    enum Mode: Equatable { case head, tail, index }

    struct Anchor {
        let position: Int
        let start: Int
    }

    struct InsertResult {
        let chain: [String?]
        let start: Int
        let nextPosition: Int
        let activeCell: Int
    }

    static func anchorBeforeRemoving(_ chain: [String?], index: Int) -> Anchor? {
        guard chain.count == 10, chain.indices.contains(index), chain[index] != nil else { return nil }
        let position = chain[..<index].compactMap { $0 }.count
        let start = chain.firstIndex(where: { $0 != nil }) == 0 ? 0 : 1
        return Anchor(position: position, start: start)
    }

    static func insert(_ module: String, chain: [String?], position: Int, start: Int, mode: Mode) -> InsertResult? {
        guard chain.count == 10 else { return nil }
        var values = chain.compactMap { $0 }
        guard values.count < 10 else { return nil }
        let insertionPosition = mode == .tail ? values.count : max(0, min(position, values.count))
        values.insert(module, at: insertionPosition)
        let preferredStart = mode == .head ? 0 : start
        let actualStart = values.count == 10 ? 0 : max(0, min(preferredStart, 10 - values.count))
        var updated = Array<String?>(repeating: nil, count: 10)
        for (offset, name) in values.enumerated() { updated[actualStart + offset] = name }
        let nextPosition = insertionPosition + 1
        return InsertResult(chain: updated, start: actualStart, nextPosition: nextPosition,
                            activeCell: min(9, actualStart + nextPosition))
    }
}

