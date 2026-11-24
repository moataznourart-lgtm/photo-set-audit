# Photo Set Audit

Audit a folder of image exports before delivery. The CSV records landscape,
portrait, and square images using their **display** dimensions, and points out
byte-for-byte duplicates without changing or deleting any source files.

## Quick start

Requires Python 3.10 or newer. From this repository:

```sh
python -m pip install .
photo-set-audit ./photos --output ./audit.csv
photo-set-audit ./photos --strict
```

The command recurses through folders and checks JPEG, PNG, WebP, TIFF, BMP, and
GIF files. It reports the first image of multi-frame files, ignores symbolic
links and special files inside the folder, and sorts paths relative to the input
folder. The CSV goes to standard output unless `--output` specifies a new
`.csv` file. An existing output file is never overwritten. Paths that could be interpreted as
spreadsheet formulas are prefixed with `./`.

- **Orientation:** both stored and display dimensions are included. EXIF values
  5–8 swap the axes; missing or out-of-range EXIF orientation is treated as
  unrotated and left blank in the CSV.
- **Duplicates:** exact SHA-256 matches point to the lexicographically first
  relative path. Resized exports, crops, and files with different metadata are
  not considered duplicates.
- **Unusable metadata:** unreadable images are kept in the CSV with an error.
  This is a metadata audit, not a full pixel-decoding or corruption test.

Exit codes: **0** for a completed report, **1** with `--strict` when a file or
subdirectory could not be read, and **2** for an invalid input folder or an
output error. Diagnostics go to standard error.

## Tests

```sh
python -m unittest discover -s tests -v
```

Tests generate their image fixtures in temporary folders. No photos are bundled.
