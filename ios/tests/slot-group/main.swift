import Foundation

func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

var selection = Set<Int>()
selection = SlotGroupSelection.toggle(group: 0, selected: selection, blocked: [])
check(selection == Set(1...5), "first group must select all eligible slots")
selection = SlotGroupSelection.toggle(group: 1, selected: selection, blocked: [])
check(selection == Set(1...10), "groups must accumulate")
selection = SlotGroupSelection.toggle(group: 0, selected: selection, blocked: [])
check(selection == Set(6...10), "second press must clear only that group")
selection = SlotGroupSelection.toggle(group: 1, selected: [6, 7, 15], blocked: [8, 10])
check(selection == Set([6, 7, 9, 15]), "blocked slots must not be selected or affect other groups")
selection = SlotGroupSelection.toggle(group: 1, selected: selection, blocked: [8, 10])
check(selection == Set([15]), "second press must clear only eligible slots")
selection = SlotGroupSelection.toggle(group: 0, selected: [3, 15], blocked: [3])
check(selection == Set([1, 2, 3, 4, 5, 15]), "previously selected blocked slots must remain untouched")
check(SlotGroupSelection.toggle(group: 5, selected: selection, blocked: []) == selection, "invalid groups must not change state")
print("Slot group selection tests passed")

