MIN_SLOT = 1
MAX_SLOT = 20


def slot_in_range(slot: int) -> bool:
    return MIN_SLOT <= int(slot) <= MAX_SLOT
