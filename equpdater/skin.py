"""Drawing for the Classic (2004) theme, as a mixin for the main window.

Everything here is presentation: it paints images and canvas items the
application already has a place for. The application decides *when* (its
hover, selection and resize logic is untouched); this decides *how*. With the
Modern theme none of it runs -- ``self._art`` is None and every caller checks
before calling in.

Images are made by :class:`equpdater.theme.ClassicArt` (Pillow, cached by
size and state) and turned into Tk photos here, once per distinct size; the
photos are kept in ``self._skin_cache`` so Tk never loses one that is still
on screen.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from . import branding
from .ui import photo_image

#: Colours of the glyphs set on stone buttons: (resting, under the cursor).
STONE_INK = {
    "link": ("#e9e1c8", "#f6d98e"),
    "release": ("#d8d1bb", "#ffffff"),
    "remove": ("#d9735f", "#ff8a70"),
    "download": ("#9bd48f", "#c6f0bd"),
}
#: Text on a stone button: normal, under the cursor, unavailable.
STONE_TEXT = ("#f2cf7a", "#ffe29a", "#8a8068")


class SkinMixin:
    """Mixed into EqUpdaterApp. Needs ``self._art`` (ClassicArt or None),
    ``self._theme``, ``self._sc`` and the app's ``_px``/``_font``."""

    _art = None

    # ── photos ────────────────────────────────────────────────────────────

    def _skin_photo(self, key, make):
        """A Tk photo for ``key``, made by ``make()`` (a Pillow image) once
        and kept alive here."""
        cache = self.__dict__.setdefault("_skin_cache", {})
        photo = cache.get(key)
        if photo is None:
            if len(cache) > 600:
                # Only the oldest are dropped; anything on screen is also
                # referenced by its canvas item or label.
                for stale in list(cache)[:200]:
                    cache.pop(stale, None)
            photo = photo_image(make(), master=self)
            cache[key] = photo
        return photo

    def _button_photo(self, big: bool, width: int, height: int, state="up"):
        return self._skin_photo(
            ("btn", big, width, height, state),
            lambda: self._art.button(big, width, height, state))

    # ── the window ────────────────────────────────────────────────────────

    def _classic_scene(self, width, height, panel_bottom):
        return self._art.scene(width, height, self._sc, self._inset,
                               panel_bottom)

    def _draw_panel_chrome(self, width, height, foot_h):
        """Gold-and-black trim round the content panels, on the window
        canvas behind them: it follows a resize with a handful of items."""
        c = self._bg_canvas
        c.delete("chrome")
        x0, y0 = self._inset, self._px(119)
        x1, y1 = width - self._inset, height - foot_h - self._px(10)
        if y1 - y0 < self._px(60):
            return
        gold, deep, hi = "#8c6a2c", "#050608", "#d4a43f"
        px = self._px
        for grow, colour, w in ((px(7), deep, px(2)), (px(5), gold, 1),
                                (px(3), deep, px(2)), (px(1), hi, 1)):
            c.create_rectangle(x0 - grow, y0 - grow, x1 + grow, y1 + grow,
                               outline=colour, width=w, tags="chrome")
        # Brackets: the ornamental corner studs of the original frame.
        s = px(5)
        for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            c.create_rectangle(cx - px(8) - s, cy - px(8) - s,
                               cx - px(8) + s, cy - px(8) + s,
                               fill="", outline="", tags="chrome")
        c.tag_raise("chrome", "bg")

    # ── header ────────────────────────────────────────────────────────────

    def _tab_cy(self) -> int:
        """Vertical centre of the tabs."""
        return self._px(82) if self._art is not None else self._px(54)

    def _gear_box(self):
        """The settings button's clickable box, top-right."""
        w = self._win_w()
        if self._art is not None:
            return (w - self._px(50), self._px(18), w - self._px(16), self._px(52))
        return (w - self._px(36), self._px(2), w - self._px(2), self._px(34))

    def _draw_logo_classic(self, hover=False):
        cv = self._hdr_canvas
        k = 0.52 * self._sc
        plaque = self._skin_photo(("plaque", round(k, 3)),
                                  lambda: self._art.plaque(k))
        left, top = self._px(18), self._px(12)
        cv.create_image(left, top, image=plaque, anchor="nw", tags="logo")
        # Where the wordmark may go: the plaque's stone.
        ix0, ix1 = left + round(46 * k), left + round(408 * k)
        cy = top + round(90 * k)
        avail = ix1 - ix0 - self._px(8)
        icon = self._logo_icon(self._px(30))
        gap = self._px(8)
        size = 22
        while True:
            font = self._font(size, bold=True)
            tw = tkfont.Font(font=font).measure(branding.APP_TITLE)
            iw = icon.width() + gap if icon is not None else 0
            if iw + tw <= avail or size <= 11:
                break
            size -= 1
        x = ix0 + (avail + self._px(8) - (iw + tw)) // 2
        if icon is not None:
            cv.create_image(x, cy, image=icon, anchor="w", tags="logo")
            x += iw
        for dx, dy in ((1, 2), (2, 2)):      # a drop shadow, as in the game
            cv.create_text(x + self._px(dx), cy + self._px(dy),
                           text=branding.APP_TITLE, font=font, fill="#000000",
                           anchor="w", tags=("logo", "logo_text"))
        cv.create_text(x, cy, text=branding.APP_TITLE, font=font,
                       fill=self._theme.colors["gold_lt"] if hover
                       else "#f6c453",
                       anchor="w", tags=("logo", "logo_text"))
        self._logo_bottom = top + plaque.height()

    def _draw_tab_classic(self, tab: str, hover: bool, width: int, cx: int):
        cv = self._hdr_canvas
        cy = self._tab_cy()
        h = self._px(34)
        state = "sel" if tab == self._active_tab else ("hover" if hover else "up")
        photo = self._button_photo(False, width, h, state)
        tag = f"nav_{tab}"
        cv.create_image(cx, cy, image=photo, tags=tag)
        return tag

    def _draw_gear_classic(self, hover=False):
        cv = self._hdr_canvas
        x0, y0, x1, y1 = self._gear_box()
        photo = self._button_photo(False, x1 - x0, y1 - y0,
                                   "hover" if hover else "up")
        cv.create_image(x0, y0, image=photo, anchor="nw", tags="gear_icon")
        cv.create_text((x0 + x1) // 2, (y0 + y1) // 2 - 1, text="⚙",
                       font=self._font(14), fill="#f2cf7a" if hover
                       else "#d4a43f", tags="gear_icon")

    # ── controls ──────────────────────────────────────────────────────────

    def _skin_icon(self, canvas, kind):
        """Put a row-action icon on a stone button. The icon was drawn in a
        20 x 18 box; the canvas grows to the button and the ink moves to its
        middle."""
        w, h = self._px(32), self._px(26)
        canvas.configure(width=w, height=h)
        canvas.move("all", (w - self._px(20)) // 2, (h - self._px(18)) // 2)
        item = canvas.create_image(
            0, 0, anchor="nw", image=self._button_photo(False, w, h, "up"),
            tags="stone")
        canvas.tag_lower("stone")
        canvas._skin_item = item

        def swap(state):
            canvas.itemconfigure(item, image=self._button_photo(False, w, h, state))
        canvas.bind("<Enter>", lambda _e: swap("hover"), add="+")
        canvas.bind("<Leave>", lambda _e: swap("up"), add="+")
        canvas.bind("<ButtonPress-1>", lambda _e: swap("down"), add="+")
        canvas.bind("<ButtonRelease-1>", lambda _e: swap("hover"), add="+")

    def _stone_button(self, parent, text, command, *, bold=True, size=10,
                      fg=None, padx=None):
        """A small stone button with a text label, packed on the right.
        Returns the canvas (the caller sets its tooltip)."""
        font = self._font(size, bold=bold)
        tw = tkfont.Font(font=font).measure(text)
        w = tw + self._px(26)
        h = self._px(26)
        c = tk.Canvas(parent, width=w, height=h, bg=parent.cget("bg"),
                      highlightthickness=0, cursor="hand2", takefocus=1)
        c.pack(side="right", padx=padx if padx is not None
               else (self._px(6), self._px(4)))
        lit_fg = fg or STONE_TEXT[0]

        def paint(state):
            c.delete("all")
            c.create_image(0, 0, anchor="nw",
                           image=self._button_photo(False, w, h, state))
            off = 1 if state == "down" else 0
            c.create_text(w // 2 + 1, h // 2 + 1 + off, text=text, font=font,
                          fill="#000000")
            c.create_text(w // 2, h // 2 + off, text=text, font=font,
                          fill=STONE_TEXT[1] if state == "hover" else lit_fg)
        paint("up")
        c.bind("<Enter>", lambda _e: paint("hover"))
        c.bind("<Leave>", lambda _e: paint("up"))
        c.bind("<ButtonPress-1>", lambda _e: paint("down"))

        def release(e):
            inside = 0 <= e.x <= w and 0 <= e.y <= h
            paint("hover" if inside else "up")
            if inside:
                command(e)
        c.bind("<ButtonRelease-1>", release)
        c.bind("<space>", lambda e: command(e))
        c.bind("<Return>", lambda e: command(e))
        return c

    def _win_w(self) -> int:           # overridden by the app (WIN_W is a
        raise NotImplementedError      # module global there)
