# EqUpdater 3 beta on Linux

**Status: the Linux build is written and its non-Linux parts are tested; it has not been built or run on Linux.** The environment this work was done in is Windows 10 with no Linux, no WSL distribution and no Docker. Everything below says which it is: *tested here*, *tested here only in simulation*, or *not tested*.

## What was done
* `tools/build_appimage_v3.py` builds `dist/EqUpdater3-v<version>-Linux-x86_64.AppImage`: the folder build from `tools/build_v3.py` (PyInstaller, pruned), an AppDir (`AppRun`, desktop entry, icon) and the pinned, checksum-verified `appimagetool` shared with the 2.x build. **No `aria2c`** (3.0 does not download the game).
* `tools/build_v3.py` now prunes both Qt layouts (Windows `Qt6X.dll` beside the modules; Linux `libQt6X.so.6` in `PySide6/Qt/lib`) and strips the host font stack on Linux, as 2.x does, so the player's own fontconfig/freetype are used.
* `.github/workflows/beta.yml` has a manual `linux` job: install the X11/xcb libraries, run the test suite offscreen, build the AppImage, run the packaged `--self-test --gui` offscreen, upload the artifact. It publishes nothing. **It has never run.**
* The launch plan (Wine, UMU or the player's own command; prefix; environment variables) is the 2.x code in `equpdater/platforms.py`, unchanged. Its 102 tests (`test_platforms`, `test_launch_options`) pass here; they simulate Linux, they do not run it.

## Dependency packaging
| Dependency | How it is satisfied |
|---|---|
| Python, PySide6/Qt Quick, certifi, the QML, skins, fonts | inside the image (PyInstaller) |
| `libxcb-cursor`, `libxcb-icccm`, `libxcb-image`, `libxcb-keysyms`, `libxcb-render-util`, `libxcb-shape`, `libxcb-xkb`, `libxcb-util`, `libxcb-xinerama`, `libxkbcommon-x11` | found with `ldd` on Qt's xcb plugin, allow-listed by name, **copied into the image** (many desktops lack `libxcb-cursor0`, which Qt 6.5+ requires) |
| glibc, libstdc++, GL/EGL and the GPU driver, X11/Wayland client libraries, dbus, **fontconfig and freetype** | the player's system (the AppImage excludelist advice; bundling a GPU stack or an old fontconfig breaks other machines). The build ubuntu-22.04 sets the oldest glibc supported |
| FUSE | not needed to run (appimagetool's static runtime); `--appimage-extract-and-run` also works |
| Wine / UMU / Proton | the player's own, as in 2.x; EqUpdater only starts them |

Tested here: the layout and file contents of the AppDir, the desktop entry, `AppRun`, the library selection logic on sample `ldd` output, the prune rules on Linux-shaped trees (`tests/test_v3_appimage.py`, 16 tests). **Not tested:** that the libraries `ldd` finds on a real build are the right set, that the image starts on any distribution.

## Not tested (needs a Linux machine, ideally several)
1. Building the AppImage at all, and the size and contents of the result.
2. Starting it: X11, **Wayland** (Qt picks the platform; the xcb plugin is bundled, the Wayland plugin comes with PySide6 but its system libraries were not examined), under a GPU, a virtual machine, and with software rendering (Settings > Low-memory rendering is the fallback if a driver misbehaves).
3. **Starting the game through Wine, UMU or Proton**: the launch plan, environment variables, prefix handling, the "game is already running" check (process list instead of file lock), `WINEPREFIX` and `Z:` path conversion, and the failure messages. Only the plan logic is tested, in simulation.
4. The credential store on Linux (Secret Service via `keyring` or `secret-tool`): tested with a fake command only; without either, a sign-in would not be remembered (and there is no sign-in service yet anyway).
5. The single-instance lock (`flock`), the crash-report hooks (`faulthandler`), file dialogs (Qt's own dialogs where the desktop has no portal), opening folders and links (`xdg-open`), HiDPI/fractional scaling under Wayland.
6. The packaged `--self-test --gui` on Linux (it is the intended CI check).
7. Fonts: whether Friz Quadrata and OpenDyslexic register through Qt on Linux, and what the Classic and Turtle WoW skins look like with the host's fallback fonts.

## How to test it when a Linux machine is available
```
sudo apt-get install -y libegl1 libgl1 libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 libfontconfig1 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-xkb1 \
  libxcb-xinerama0 libxcb-util1
python3 -m pip install pillow certifi pyside6 pyinstaller psutil
python3 tools/build_appimage_v3.py
APPIMAGE_EXTRACT_AND_RUN=1 QT_QPA_PLATFORM=offscreen dist/EqUpdater3-*.AppImage --self-test=/tmp/st.json --gui && cat /tmp/st.json
dist/EqUpdater3-*.AppImage            # then work through docs/v3/QA_CHECKLIST.md on a real desktop, with a real client under Wine
```
Report what differs. Data lives in `~/.local/share/EqUpdater` (shared with 2.x on Linux, as on Windows).
