import csv
import io
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "photo_set_audit.py"


def make_image(path, size=(40, 20), *, orientation=None, color="navy"):
    with Image.new("RGB", size, color) as image:
        if orientation is None:
            image.save(path)
        else:
            exif = image.getexif()
            exif[274] = orientation
            image.save(path, exif=exif)


class TestCLI(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.work = Path(work.name)
        self.root = self.work / "photos"
        self.root.mkdir()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.root), *map(str, args)],
            capture_output=True, text=True, check=False,
        )

    def test_exif_and_duplicate_interaction(self):
        # Same bytes and EXIF under different directory/name/case combinations:
        # the duplicate must keep the EXIF-correct portrait display dimensions.
        (self.root / "a").mkdir()
        first = self.root / "a" / "rotated.JPG"
        make_image(first, orientation=6)
        (self.root / "duplicate.jpg").write_bytes(first.read_bytes())
        make_image(self.root / "same-pixels.jpg")
        make_image(self.root / "square.png", (18, 18))
        make_image(self.root / "mirrored.png", orientation=5)

        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = list(csv.DictReader(io.StringIO(proc.stdout)))
        self.assertEqual([row["path"] for row in rows], [
            "a/rotated.JPG", "duplicate.jpg", "mirrored.png", "same-pixels.jpg", "square.png",
        ])
        index = {row["path"]: row for row in rows}
        for name in ("a/rotated.JPG", "duplicate.jpg"):
            row = index[name]
            self.assertEqual((row["stored_width"], row["stored_height"]), ("40", "20"))
            self.assertEqual((row["display_width"], row["display_height"]), ("20", "40"))
            self.assertEqual(row["orientation"], "portrait")
            self.assertEqual(row["exif_orientation"], "6")
        self.assertEqual(index["duplicate.jpg"]["duplicate_of"], "a/rotated.JPG")
        self.assertEqual(index["duplicate.jpg"]["sha256"], index["a/rotated.JPG"]["sha256"])
        self.assertEqual(index["mirrored.png"]["orientation"], "portrait")
        self.assertEqual(index["same-pixels.jpg"]["orientation"], "landscape")
        self.assertEqual(index["same-pixels.jpg"]["duplicate_of"], "")
        self.assertEqual(index["square.png"]["orientation"], "square")

    def test_strict_still_reports_good_and_bad_files(self):
        good = self.root / "good.png"
        make_image(good)
        (self.root / "broken.jpg").write_bytes(b"not an image")
        (self.root / "notes.txt").write_text("Ignore this", encoding="utf-8")
        proc = self.run_cli("--strict")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        rows = list(csv.DictReader(io.StringIO(proc.stdout)))
        self.assertEqual([row["path"] for row in rows], ["broken.jpg", "good.png"])
        self.assertTrue(rows[0]["error"])
        self.assertFalse(rows[1]["error"])
        self.assertEqual(self.run_cli().returncode, 0)

    def test_pillow_tiff_does_not_get_rotated_twice(self):
        make_image(self.root / "rotated.tiff", orientation=8)
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        row, = csv.DictReader(io.StringIO(proc.stdout))
        self.assertEqual((row["stored_width"], row["stored_height"]), ("40", "20"))
        self.assertEqual((row["display_width"], row["display_height"]), ("20", "40"))
        self.assertEqual(row["orientation"], "portrait")

    def test_input_photos_are_not_overwritten(self):
        source = self.root / "safe.png"
        make_image(source)
        before = source.read_bytes()
        proc = self.run_cli("-o", source)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(source.read_bytes(), before)
        not_a_report = self.root / "new.jpg"
        self.assertEqual(self.run_cli("-o", not_a_report).returncode, 2)
        self.assertFalse(not_a_report.exists())

    def test_output_file_and_formula_safe_paths(self):
        make_image(self.root / "=image.png")
        (self.root / "backup.png").write_bytes((self.root / "=image.png").read_bytes())
        report = self.work / "result.csv"
        proc = self.run_cli("--output", report)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        with report.open(encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(rows[0]["path"], "./=image.png")
        self.assertEqual(rows[1]["duplicate_of"], "./=image.png")
        data = report.read_bytes()
        self.assertEqual(self.run_cli("--output", report).returncode, 2)
        self.assertEqual(report.read_bytes(), data)

    def test_linked_paths_do_not_escape_scan_root(self):
        outside = self.work / "outside"
        outside.mkdir()
        make_image(outside / "remote.png")
        try:
            (self.root / "linked.png").symlink_to(outside / "remote.png")
            (self.root / "shortcut").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(list(csv.DictReader(io.StringIO(proc.stdout))), [])


if __name__ == "__main__":
    unittest.main()
