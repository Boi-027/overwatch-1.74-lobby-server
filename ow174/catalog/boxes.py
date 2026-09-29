"""Loot box types of Overwatch 1."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BoxType:
    name: str  # the official name
    label: str  # the short name shown in the dashboard
    event: int = 0  # event pool the box draws from: 0 base items, 1-6 the seasonal events
    legendary: bool = False  # always contains a legendary item
    starter: int = 0  # how many a new profile starts with


BOX_TYPES = {
    0: BoxType("Standard", "Standard", starter=50),
    1: BoxType("Summer Games", "Summer Games", event=1, starter=10),
    2: BoxType("Halloween Terror", "Halloween", event=2, starter=10),
    3: BoxType("Winter Wonderland", "Winter Wonderland", event=3, starter=10),
    4: BoxType("Lunar New Year", "Lunar New Year", event=4, starter=10),
    5: BoxType("Archives", "Archives", event=5, starter=10),
    6: BoxType("Anniversary", "Anniversary", event=6, starter=10),
    7: BoxType("Golden", "Golden", legendary=True, starter=10),
    9: BoxType("Legendary Anniversary", "Legendary Anniversary", event=6, legendary=True, starter=10),
    10: BoxType("Wrecking Ball", "Ram"),
    12: BoxType("Legendary", "Legendary", legendary=True, starter=10),
}


def box_name(box_type: int) -> str:
    box = BOX_TYPES.get(box_type)
    return box.name if box else f"Type {box_type}"


def starter_boxes(first_id: int = 1) -> tuple[list[dict], int]:
    """A new profile's boxes as [{"id", "type", "name"}] and the next free box id."""
    boxes = []
    next_id = first_id
    for box_type, box in BOX_TYPES.items():
        for _ in range(box.starter):
            boxes.append({"id": next_id, "type": box_type, "name": box.name})
            next_id += 1
    return boxes, next_id
