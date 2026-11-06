import Foundation

enum SlotGroupSelection {
    static func toggle(group: Int, selected: Set<Int>, blocked: Set<Int>) -> Set<Int> {
        guard (0..<4).contains(group) else { return selected }
        let first = group * 5 + 1
        let eligible = Set(first..<(first + 5)).subtracting(blocked)
        guard !eligible.isEmpty else { return selected }
        if eligible.isSubset(of: selected) {
            return selected.subtracting(eligible)
        }
        return selected.union(eligible)
    }
}

