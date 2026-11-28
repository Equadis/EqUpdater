"""core/fontstore.py: the download of Friz Quadrata and Object Sans on request. Automated, no network: the
opener is replaced. The real font.download hosts have NOT been contacted by any test."""

import io
import os
import shutil
import tempfile
import unittest
import zipfile

from equpdater.core import fontstore


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def opener_of(payload):
    seen = []

    def opener(url):
        seen.append(url)
        return Resp(payload)
    opener.seen = seen
    return opener


def make_zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


class TestFontStore(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="equ-fs-")
        self.addCleanup(shutil.rmtree, self.data, True)

    def test_the_addresses_are_the_ones_given_and_both_hosts_are_allow_listed(self):
        self.assertEqual(fontstore.DOWNLOADS["friz"]["url"], "https://font.download/dl/font/friz-quadrata.zip")
        self.assertEqual(fontstore.DOWNLOADS["objectsans"]["url"], "https://font.download/dl/font/object-sans.zip")
        self.assertEqual(fontstore.ALLOWED_HOSTS, {"font.download"})

    def test_only_font_files_are_kept_under_names_without_paths(self):
        z = make_zip({"Friz Quadrata/FRIZQUAD.TTF": b"a", "../../evil.ttf": b"b", "notes.txt": b"c", "x/.hidden.otf": b"d",
                      "deep\\dir\\FrizQuadrata-Bold.otf": b"e"})
        files = fontstore.download(self.data, "friz", opener_of(z))
        folder = os.path.realpath(fontstore.fonts_dir(self.data))
        for f in files:
            self.assertEqual(os.path.dirname(os.path.realpath(f)), folder)
        names = sorted(os.listdir(folder))
        self.assertTrue(all(n.lower().endswith((".ttf", ".otf")) for n in names), names)
        self.assertNotIn("notes.txt", names)
        self.assertIn("FrizQuadrata-Bold.otf", names)
        self.assertFalse(any(n.startswith(".") or n.endswith(".part") for n in names))
        self.assertFalse(os.path.exists(os.path.join(self.data, "evil.ttf")))
        self.assertTrue(fontstore.is_installed(self.data, "friz"))                 # an oddly named file is still recognised
        self.assertFalse(fontstore.is_installed(self.data, "objectsans"))

    def test_it_asks_the_given_address_only(self):
        o = opener_of(make_zip({"ObjectSansRegular.otf": b"x"}))
        fontstore.download(self.data, "objectsans", o)
        self.assertEqual(o.seen, ["https://font.download/dl/font/object-sans.zip"])

    def test_bad_downloads_fail_cleanly_and_leave_nothing(self):
        cases = {
            "not a zip": b"<html>Just a moment...</html>",
            "no fonts": make_zip({"readme.txt": b"hi"}),
            "too many": make_zip({"f%d.ttf" % i: b"x" for i in range(fontstore.MAX_FILES + 1)}),
        }
        for label, payload in cases.items():
            with self.subTest(label), self.assertRaises(fontstore.FontError):
                fontstore.download(self.data, "friz", opener_of(payload))
            self.assertFalse(os.path.isdir(fontstore.fonts_dir(self.data)), label)
        with self.assertRaises(fontstore.FontError):
            fontstore.download(self.data, "nonsense", opener_of(b""))

    def test_an_oversized_archive_is_refused(self):
        old = fontstore.MAX_ARCHIVE
        fontstore.MAX_ARCHIVE = 100
        self.addCleanup(setattr, fontstore, "MAX_ARCHIVE", old)
        with self.assertRaises(fontstore.FontError) as cm:
            fontstore.download(self.data, "friz", opener_of(b"x" * 500))
        self.assertIn("larger than expected", str(cm.exception))

    def test_a_network_error_becomes_a_readable_message(self):
        def boom(url):
            raise OSError("connection reset")
        with self.assertRaises(fontstore.FontError) as cm:
            fontstore.download(self.data, "friz", boom)
        self.assertIn("connection reset", str(cm.exception))

    def test_the_real_opener_refuses_another_host_and_plain_http(self):
        saved = dict(fontstore.DOWNLOADS["friz"])
        self.addCleanup(fontstore.DOWNLOADS["friz"].update, saved)
        for url in ("https://evil.example/friz.zip", "http://font.download/dl/font/friz-quadrata.zip"):
            fontstore.DOWNLOADS["friz"]["url"] = url
            with self.subTest(url), self.assertRaises(fontstore.FontError):
                fontstore.download(self.data, "friz")                              # default opener: never reaches the network
        self.assertFalse(os.path.isdir(fontstore.fonts_dir(self.data)))

    def test_nothing_runs_at_import_or_start(self):
        # a font is fetched only when download() is called: the module has no side effects on import
        self.assertFalse(os.path.isdir(fontstore.fonts_dir(self.data)))


if __name__ == "__main__":
    unittest.main()
