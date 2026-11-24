import Foundation

func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

func chain(_ names: [String], start: Int) -> [String?] {
    var values = Array<String?>(repeating: nil, count: 10)
    for (offset, name) in names.enumerated() { values[start + offset] = name }
    return values
}

func replace(_ values: [String?], index: Int, with name: String) -> [String?] {
    guard let anchor = ModuleChainEdit.anchorBeforeRemoving(values, index: index) else {
        fatalError("missing removal anchor")
    }
    var removed = values
    removed[index] = nil
    guard let result = ModuleChainEdit.insert(name, chain: removed, position: anchor.position,
                                              start: anchor.start, mode: .index) else {
        fatalError("insert failed")
    }
    return result.chain
}

let seven = ["A", "B", "C", "D", "E", "F", "G"]
for start in [0, 1] {
    let original = chain(seven, start: start)
    let replaced = replace(original, index: start + 6, with: "NEW")
    check(replaced == chain(["A", "B", "C", "D", "E", "F", "NEW"], start: start),
          "replacing the seventh module must leave it last and retain its original cell")
    check(replace(original, index: start + 3, with: "NEW") ==
          chain(["A", "B", "C", "NEW", "E", "F", "G"], start: start),
          "middle replacement must retain relative order")
    check(replace(original, index: start, with: "NEW") ==
          chain(["NEW", "B", "C", "D", "E", "F", "G"], start: start),
          "first replacement must remain first")
}
let original = chain(["A", "B"], start: 1)
let tail = ModuleChainEdit.insert("C", chain: original, position: 0, start: 1, mode: .tail)
check(tail?.chain == chain(["A", "B", "C"], start: 1), "tail insertion must stay at the end")
let head = ModuleChainEdit.insert("X", chain: original, position: 0, start: 1, mode: .head)
check(head?.chain == chain(["X", "A", "B"], start: 0), "head insertion must stay first")
let full = chain((1...10).map(String.init), start: 0)
check(ModuleChainEdit.insert("X", chain: full, position: 9, start: 0, mode: .tail) == nil,
      "a full chain must not accept another module")
check(ModuleChainEdit.anchorBeforeRemoving(original, index: 0) == nil, "empty slots cannot be removed")
print("Module chain editing tests passed")

