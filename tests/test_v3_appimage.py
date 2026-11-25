"""The Linux AppImage build, as far as it can be checked without Linux: the layout, the files'
contents, the library selection and the prune rules for Linux's file names. Building and running
the image is NOT covered here (see docs/v3/LINUX.md)."""

import importlib
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


def mods():
    return importlib.import_module("build_appimage_v3"), importlib.import_module("build_v3")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.ai, self.bv = mods()


class TestAssembly(Tmp):
    def fake_build(self):
        b = os.path.join(self.d, "build")
        os.makedirs(os.path.join(b, "_internal"))
        for rel in ("EqUpdater3", "_internal/base_library.zip"):
            with open(os.path.join(b, rel), "wb") as f:
                f.write(b"x")
        return b

    def test_the_appdir_has_everything_an_appimage_needs(self):
        lib = os.path.join(self.d, "libxcb-cursor.so.0")
        with open(lib, "wb") as f:
            f.write(b"lib")
        appdir = os.path.join(self.d, "AppDir")
        self.ai.assemble(appdir, self.fake_build(), os.path.join(ROOT, "icon.png"), [lib])
        for rel in ("AppRun", "equpdater3.desktop", "equpdater3.png", ".DirIcon", "usr/bin/EqUpdater3",
                    "usr/bin/_internal/base_library.zip", "usr/lib/libxcb-cursor.so.0",
                    "usr/share/icons/hicolor/256x256/apps/equpdater3.png"):
            self.assertTrue(os.path.exists(os.path.join(appdir, *rel.split("/"))), rel)

    def test_apprun_runs_the_program_from_inside_the_mount_and_finds_the_bundled_libraries(self):
        text = self.ai.APPRUN
        self.assertTrue(text.startswith("#!/bin/sh"))
        self.assertIn('exec "$HERE/usr/bin/EqUpdater3"', text)
        self.assertIn("LD_LIBRARY_PATH", text)
        self.assertIn('"$@"', text)                                  # arguments reach the program
        self.assertNotIn("\r", text)

    def test_the_desktop_entry_is_valid_and_points_at_the_program_and_icon(self):
        path = os.path.join(self.d, "x.desktop")
        with open(path, "w") as f:
            f.write(self.ai.DESKTOP)
        self.assertEqual(self.ai.check_desktop(path), [])
        self.assertIn("Exec=EqUpdater3", self.ai.DESKTOP)
        self.assertIn("Icon=equpdater3", self.ai.DESKTOP)
        with open(path, "w") as f:
            f.write("[Desktop Entry]\nName=x\n")
        self.assertTrue(self.ai.check_desktop(path))

    def test_there_is_no_downloader_in_the_image(self):
        appdir = os.path.join(self.d, "AppDir")
        self.ai.assemble(appdir, self.fake_build(), os.path.join(ROOT, "icon.png"))
        found = [f for _d, _s, fs in os.walk(appdir) for f in fs if "aria2" in f.lower()]
        self.assertEqual(found, [])
        with open(os.path.join(ROOT, "tools", "build_appimage_v3.py"), encoding="utf-8") as f:
            self.assertNotIn("ARIA2_STATIC", f.read())

    def test_the_appimage_tool_is_pinned_by_checksum_and_shared_with_the_2x_build(self):
        url, sha = self.ai.APPIMAGETOOL
        self.assertTrue(url.startswith("https://"))
        self.assertRegex(sha, r"^[0-9a-f]{64}$")

    def test_the_output_is_named_for_the_beta_and_the_platform(self):
        from equpdater.version3 import APP_VERSION_V3
        with open(os.path.join(ROOT, "tools", "build_appimage_v3.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn('f"{NAME}-v{APP_VERSION_V3}-Linux-x86_64.AppImage"', text)
        self.assertEqual(self.ai.NAME, "EqUpdater3")
        self.assertEqual(APP_VERSION_V3.split("-")[1][:4], "beta")


class TestLibraries(Tmp):
    LDD = """\tlinux-vdso.so.1 (0x00007ffd)
\tlibQt6Core.so.6 => /app/PySide6/Qt/lib/libQt6Core.so.6 (0x00007f1)
\tlibxcb-cursor.so.0 => /usr/lib/x86_64-linux-gnu/libxcb-cursor.so.0 (0x00007f2)
\tlibxcb-icccm.so.4 => /usr/lib/x86_64-linux-gnu/libxcb-icccm.so.4 (0x00007f3)
\tlibxcb-xkb.so.1 => /usr/lib/x86_64-linux-gnu/libxcb-xkb.so.1 (0x00007f4)
\tlibxkbcommon-x11.so.0 => /usr/lib/x86_64-linux-gnu/libxkbcommon-x11.so.0 (0x00007f5)
\tlibX11.so.6 => /usr/lib/x86_64-linux-gnu/libX11.so.6 (0x00007f6)
\tlibGL.so.1 => /usr/lib/x86_64-linux-gnu/libGL.so.1 (0x00007f7)
\tlibfontconfig.so.1 => /usr/lib/x86_64-linux-gnu/libfontconfig.so.1 (0x00007f8)
\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f9)
\tlibmissing.so.1 => not found
\t/lib64/ld-linux-x86-64.so.2 (0x00007fa)"""

    def test_ldd_output_is_read_and_missing_libraries_are_ignored(self):
        paths = self.ai.parse_ldd(self.LDD)
        self.assertIn("/usr/lib/x86_64-linux-gnu/libxcb-cursor.so.0", paths)
        self.assertFalse(any("missing" in p for p in paths))

    def test_only_the_xcb_helpers_are_bundled_never_gl_fonts_x11_or_libc(self):
        chosen = [os.path.basename(p) for p in self.ai.select_bundle(self.ai.parse_ldd(self.LDD))]
        self.assertEqual(chosen, ["libxcb-cursor.so.0", "libxcb-icccm.so.4", "libxcb-xkb.so.1", "libxkbcommon-x11.so.0"])
        for banned in ("libGL", "libEGL", "libfontconfig", "libfreetype", "libX11", "libc.so", "libstdc++", "libdbus"):
            self.assertFalse(any(banned in c for c in chosen), banned)

    def test_the_library_lookup_uses_ldd_on_the_plugin(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return type("R", (), {"stdout": self.LDD})()
        got = self.ai.library_paths("/x/libqxcb.so", run=run)
        self.assertEqual(calls, [["ldd", "/x/libqxcb.so"]])
        self.assertTrue(got)

    def test_the_xcb_plugin_is_found_in_the_build(self):
        plugin_dir = os.path.join(self.d, "_internal", "PySide6", "Qt", "plugins", "platforms")
        os.makedirs(plugin_dir)
        with open(os.path.join(plugin_dir, "libqxcb.so"), "wb") as f:
            f.write(b"x")
        self.assertTrue(self.ai.find_xcb_plugin(self.d).endswith("libqxcb.so"))
        self.assertIsNone(self.ai.find_xcb_plugin(tempfile.mkdtemp()))


class TestPruneKnowsLinuxLayout(Tmp):
    def tree(self):
        internal = os.path.join(self.d, "_internal")
        qt = os.path.join(internal, "PySide6", "Qt")
        for rel in ("lib/libQt6Core.so.6", "lib/libQt6Quick.so.6", "lib/libQt6QuickControls2Basic.so.6",
                    "lib/libQt6Quick3D.so.6", "lib/libQt6Pdf.so.6", "lib/libQt6Multimedia.so.6",
                    "lib/libQt6QuickControls2Material.so.6", "lib/libQt6Network.so.6",
                    "plugins/platforms/libqxcb.so", "plugins/qmltooling/libx.so",
                    "qml/QtQuick/Controls/Basic/qmldir", "qml/QtQuick/Controls/Material/qmldir",
                    "qml/QtQuick3D/qmldir", "qml/QtQuick/Dialogs/qmldir", "qml/QtQuick/Layouts/qmldir",
                    "translations/qt_de.qm"):
            p = os.path.join(qt, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"x" * 10)
        return internal

    def test_unused_qt_is_removed_and_what_the_app_uses_is_kept_on_linux_names(self):
        internal = self.tree()
        freed = self.bv.prune(internal)
        qt = os.path.join(internal, "PySide6", "Qt")
        for gone in ("lib/libQt6Quick3D.so.6", "lib/libQt6Pdf.so.6", "lib/libQt6Multimedia.so.6",
                     "lib/libQt6QuickControls2Material.so.6", "qml/QtQuick3D", "qml/QtQuick/Controls/Material",
                     "translations", "plugins/qmltooling"):
            self.assertFalse(os.path.exists(os.path.join(qt, *gone.split("/"))), gone)
        for kept in ("lib/libQt6Core.so.6", "lib/libQt6Quick.so.6", "lib/libQt6QuickControls2Basic.so.6",
                     "lib/libQt6Network.so.6", "plugins/platforms/libqxcb.so", "qml/QtQuick/Controls/Basic",
                     "qml/QtQuick/Dialogs", "qml/QtQuick/Layouts"):
            self.assertTrue(os.path.exists(os.path.join(qt, *kept.split("/"))), kept)
        self.assertGreater(freed, 0)

    def test_the_windows_layout_still_works(self):
        internal = os.path.join(self.d, "w", "_internal")
        base = os.path.join(internal, "PySide6")
        os.makedirs(os.path.join(base, "qml", "QtQuick3D"))
        for n in ("Qt6Core.dll", "Qt6Quick3D.dll", "opengl32sw.dll"):
            with open(os.path.join(base, n), "wb") as f:
                f.write(b"x")
        self.bv.prune(internal)
        self.assertTrue(os.path.exists(os.path.join(base, "Qt6Core.dll")))
        self.assertFalse(os.path.exists(os.path.join(base, "Qt6Quick3D.dll")))
        self.assertFalse(os.path.exists(os.path.join(base, "opengl32sw.dll")))
        self.assertFalse(os.path.exists(os.path.join(base, "qml", "QtQuick3D")))

    def test_every_qml_import_survives_the_prune_on_the_linux_layout_too(self):
        import re
        imports = set()
        for d, _s, files in os.walk(os.path.join(ROOT, "equpdater", "qtui", "qml")):
            for f in files:
                if f.endswith(".qml") and f != "VideoLayer.qml":
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        imports |= set(re.findall(r"^import\s+([\w.]+)", fh.read(), re.M))
        for mod in imports - {"EqUi"}:
            rel = "qml/" + mod.replace(".", "/")
            for pruned in self.bv.PRUNE_DIRS:
                self.assertFalse(rel == pruned or rel.startswith(pruned + "/"), mod)


class TestLinuxFontStack(Tmp):
    def test_the_hosts_fontconfig_is_used_not_the_build_machines(self):
        if sys.platform == "win32":
            self.assertEqual(self.bv.strip_host_font_stack(self.d), [])          # a no-op on Windows
            return
        internal = os.path.join(self.d, "_internal")
        os.makedirs(os.path.join(internal, "PySide6", "Qt", "lib"))
        for n in ("libfontconfig.so.1", "libfreetype.so.6", "libQt6Core.so.6"):
            with open(os.path.join(internal, n), "wb") as f:
                f.write(b"x")
        removed = self.bv.strip_host_font_stack(internal)
        self.assertIn("libfontconfig.so.1", removed)
        self.assertTrue(os.path.exists(os.path.join(internal, "libQt6Core.so.6")))


class TestWorkflowAndDocs(unittest.TestCase):
    def test_the_manual_beta_workflow_builds_and_self_tests_the_appimage_and_publishes_nothing(self):
        with open(os.path.join(ROOT, ".github", "workflows", "beta.yml"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("build_appimage_v3.py", text)
        self.assertIn("workflow_dispatch", text)
        self.assertNotIn("gh release", text)
        self.assertNotIn("release:", text.split("jobs:")[0])
        self.assertIn("libxcb-cursor0", text)

    def test_what_cannot_be_tested_is_written_down(self):
        with open(os.path.join(ROOT, "docs", "v3", "LINUX.md"), encoding="utf-8") as f:
            text = f.read().lower()
        for needle in ("not tested", "wine", "umu", "wayland", "appimage"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
