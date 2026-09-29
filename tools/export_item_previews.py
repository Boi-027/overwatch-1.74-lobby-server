"""Make the dashboard's icon and spray previews from a DataTool export of the game.

1. Export the pictures with DataTool (OWLib) from an Overwatch 1.74 install:

    DataTool.exe <game> extract-unlocks <out> --skip-models --skip-sound --skip-animations
        --convert-textures-type=png --language=enUS "*|spray=*|icon=*"
    DataTool.exe <game> extract-general <out> --skip-models --skip-sound --skip-animations
        --convert-textures-type=png --language=enUS

2. Run this script on <out> (it needs Pillow: py -m pip install pillow):

    py tools/export_item_previews.py <out>

It writes one small WebP per item, named by its GUID, to ow174/dashboard/web/assets/previews/.
DataTool names the files after the item, so they are matched to the catalog by hero, type and name.
Portrait frames are drawn from two layers: the tier's border for the level step, and the star
strip on its lower half.
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ow174.catalog.items import ItemDB  # noqa: E402
from ow174.catalog.templates import RetailTemplates  # noqa: E402
from ow174.content.collection import Collection, frame_parts  # noqa: E402

OUT_DIR = ROOT / "ow174" / "dashboard" / "web" / "assets" / "previews"
TYPES = ("Icon", "Spray")
SIZE = 128
QUALITY = 80
# Frame tiers from level 1 up, 600 levels each; DataTool spells Silver as "Sliver".
FRAME_TIERS = ("Bronze", "Sliver", "Gold", "Platinum", "Diamond")
DUPLICATE_SUFFIX = re.compile(r"\.[0-9A-F]{3}$")  # DataTool adds the GUID type to a repeated name


def match_key(name: str) -> str:
    """The name reduced to letters and digits. File names lose characters such as '?' or a final '.'."""
    name = DUPLICATE_SUFFIX.sub("", name)
    return re.sub(r"[\W_]+", "", name.casefold())


def exported_files(export: Path) -> list[tuple[str | None, str, str, Path]]:
    """(hero, type, name, file) for each exported icon and spray.

    Heroes/<hero>/<type>/<event>/<rarity>/<name>.png, and General/<type>/<event>/[<rarity>/]<name>.png.
    """
    files = []
    for path in export.rglob("*.png"):
        parts = path.relative_to(export).parts
        if parts[0] == "Heroes" and len(parts) == 6:
            hero, kind = parts[1], parts[2]
        elif parts[0] == "General" and len(parts) in (4, 5):
            hero, kind = None, parts[1]
        else:
            continue
        if kind in TYPES:
            files.append((hero, kind, path.stem, path))
    return files


def catalog_by_key(items: ItemDB) -> dict[tuple, list[int]]:
    by_key = defaultdict(list)
    for unlock in items.unlocks.values():
        if unlock.type in TYPES and unlock.name:
            hero = match_key(unlock.hero) if unlock.hero else None
            by_key[(hero, unlock.type, match_key(unlock.name))].append(unlock.guid)
    return by_key


def save_preview(image: Image.Image, target: Path) -> None:
    image = image.convert("RGBA")
    image.thumbnail((SIZE, SIZE))
    image.save(target, "WEBP", quality=QUALITY, method=6)


def export_frames(export: Path, border_levels: list[tuple[int, int]]) -> int:
    """One picture per portrait frame GUID. A frame's look follows its unlock level: the tier (600
    levels), the stars (100 levels) and the border step (10 levels). Its GUID does not."""
    frames = export / "General" / "PortraitFrame"
    written = 0
    for level, guid in border_levels:
        tier_index, stars, step = frame_parts(level)
        tier = FRAME_TIERS[tier_index]
        with Image.open(frames / tier / f"Border - {step}.png") as image:
            frame = image.convert("RGBA")
        if stars:
            # The star strip is half as tall as the border and sits on its lower half.
            with Image.open(frames / tier / f"Star - {stars}.png") as star:
                strip = star.convert("RGBA")
            frame.alpha_composite(strip, (0, frame.height - strip.height))
        save_preview(frame, OUT_DIR / f"{guid:016X}.webp")
        written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("export", type=Path, help="the folder DataTool exported to")
    args = parser.parse_args()

    items = ItemDB()
    by_key = catalog_by_key(items)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written, unmatched = 0, []
    for hero, kind, name, path in exported_files(args.export):
        guids = by_key.get((match_key(hero) if hero else None, kind, match_key(name)), [])
        if not guids:
            unmatched.append(path.relative_to(args.export))
            continue
        # Items with the same hero, type and name look the same, so they share the picture.
        with Image.open(path) as image:
            for guid in guids:
                save_preview(image, OUT_DIR / f"{guid:016X}.webp")
                written += 1
    frames = export_frames(args.export, Collection(RetailTemplates(), items).border_levels)
    all_guids = [guid for guids in by_key.values() for guid in guids]
    missing = sum(1 for guid in all_guids if not (OUT_DIR / f"{guid:016X}.webp").exists())
    print(f"{written} icon and spray previews and {frames} frames written to {OUT_DIR}")
    print(f"{len(unmatched)} exported files matched no catalog item, {missing} catalog items have no preview")
    for path in unmatched[:20]:
        print("  unmatched:", ascii(str(path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
