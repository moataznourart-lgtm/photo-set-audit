"""Scan still-photo files and report display orientation and exact duplicates."""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
import stat
import sys
from typing import BinaryIO, Iterable, Sequence, TextIO
import warnings

from PIL import Image, UnidentifiedImageError

EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".jpe", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
)
# Values 5–8 all transpose the stored raster's axes, including mirrored modes.
SWAPS_AXES = frozenset({5, 6, 7, 8})
FIELDS = (
    "path", "stored_width", "stored_height", "display_width", "display_height",
    "orientation", "exif_orientation", "bytes", "sha256", "duplicate_of", "error",
)


@dataclass
class Finding:
    path: str
    stored_width: int | str = ""
    stored_height: int | str = ""
    display_width: int | str = ""
    display_height: int | str = ""
    orientation: str = ""
    exif_orientation: int | str = ""
    bytes: int | str = ""
    sha256: str = ""
    duplicate_of: str = ""
    error: str = ""

    def as_row(self) -> dict[str, int | str]:
        row = dict(vars(self))
        # Prevent paths like =HYPERLINK(...).jpg opening as spreadsheet formulas.
        for key in ("path", "duplicate_of"):
            value = str(row[key])
            if value and (value[0] in "\t\r\n" or value.lstrip().startswith(("=", "+", "-", "@"))):
                row[key] = "./" + value
        return row


def sha256_from_open_file(source: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def read_photo(root: Path, path: Path) -> Finding:
    result = Finding(path.relative_to(root).as_posix())
    try:
        # Open only once for dimensions/hash; a concurrent pathname replacement
        # cannot cause the hash to be taken from a different file handle.
        with path.open("rb") as source:
            before = os.fstat(source.fileno())
            result.bytes = before.st_size
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(source) as image:
                    width, height = image.size
                    if image.format == "TIFF":
                        # Newer Pillow versions already set image.size to
                        # displayed TIFF dimensions; get the unrotated tags.
                        width = int(image.tag_v2.get(256, width))
                        height = int(image.tag_v2.get(257, height))
                    exif = image.getexif().get(274)
            source.seek(0)
            digest = sha256_from_open_file(source)
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                result.error = "File changed during scan"
                return result
        result.stored_width, result.stored_height = width, height
        if isinstance(exif, int) and 1 <= exif <= 8:
            result.exif_orientation = exif
        if result.exif_orientation in SWAPS_AXES:
            width, height = height, width
        result.display_width, result.display_height = width, height
        result.orientation = "landscape" if width > height else "portrait" if width < height else "square"
        result.sha256 = digest
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        result.error = "Image dimensions exceed Pillow's safety limit"
    except UnidentifiedImageError:
        result.error = "Unrecognized or corrupt image"
    except (OSError, SyntaxError, TypeError, ValueError, EOFError, OverflowError):
        result.error = "Image metadata or file could not be read"
    return result


def media_paths(root: Path, walk_errors: list[str]) -> Iterable[Path]:
    def walk_error(error: OSError) -> None:
        name = getattr(error, "filename", None)
        if name:
            try:
                name = Path(name).relative_to(root).as_posix()
            except ValueError:
                name = "(root)"
        walk_errors.append(f"Could not scan directory: {name or '(root)'}")

    paths = []
    for folder, _, files in os.walk(root, onerror=walk_error, followlinks=False):
        for name in files:
            candidate = Path(folder) / name
            if candidate.suffix.lower() not in EXTENSIONS:
                continue
            try:
                # Ignore symlinks, sockets and named pipes rather than opening
                # external targets or blocking indefinitely.
                if stat.S_ISREG(candidate.lstat().st_mode):
                    paths.append(candidate)
            except OSError:
                walk_errors.append(f"Could not inspect path: {candidate.relative_to(root).as_posix()}")
    return sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def scan(root: Path, walk_errors: list[str]) -> Iterable[Finding]:
    first_by_hash: dict[str, str] = {}
    for path in media_paths(root, walk_errors):
        finding = read_photo(root, path)
        if finding.sha256:
            first = first_by_hash.setdefault(finding.sha256, finding.path)
            if first != finding.path:
                finding.duplicate_of = first
        yield finding


def report(root: Path, output: TextIO, walk_errors: list[str]) -> int:
    writer = csv.DictWriter(output, FIELDS, lineterminator="\r\n")
    writer.writeheader()
    errors = 0
    for finding in scan(root, walk_errors):
        writer.writerow(finding.as_row())
        errors += bool(finding.error)
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="photo folder to scan recursively")
    parser.add_argument("-o", "--output", type=Path, help="new CSV file (default: stdout; never overwrites)")
    parser.add_argument("--strict", action="store_true", help="exit 1 if files or directories were unreadable")
    args = parser.parse_args(argv)
    if not args.folder.is_dir():
        parser.error("folder must be a readable directory")
    if args.output is not None and args.output.suffix.lower() != ".csv":
        parser.error("--output must end in .csv")

    walk_errors: list[str] = []
    try:
        if args.output is None:
            errors = report(args.folder, sys.stdout, walk_errors)
        else:
            # Exclusive creation prevents overwriting unrelated files or photos.
            with args.output.open("x", encoding="utf-8", newline="") as file:
                errors = report(args.folder, file, walk_errors)
    except OSError as error:
        print(f"photo-set-audit: {error}", file=sys.stderr)
        return 2

    for error in walk_errors:
        print(f"photo-set-audit: {error}", file=sys.stderr)
    if errors:
        print(f"photo-set-audit: {errors} unreadable image(s); see the CSV error column", file=sys.stderr)
    return int(args.strict and bool(walk_errors or errors))


if __name__ == "__main__":
    raise SystemExit(main())
