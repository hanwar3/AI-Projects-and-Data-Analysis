"""
oblique.py — perspective 3D terrain rasteriser plus page composition.

Renders a draped DEM as a tilted slab with true height displacement and occlusion,
using a painter's algorithm: iterate rows from the far edge toward the camera and
splat each row with a vertical skirt that fills the gap to the next row. Nearer rows
are drawn later, so they correctly hide the terrain behind them.

Why not a GPU engine: the obvious candidates either ignore camera tilt (rendering a
flat top-down map) or need minutes per frame on integrated GPUs. This does a 4K frame
in about three seconds on CPU and gives exact control over the look.

    from oblique import Camera, render, compose
    rgb, cov = render(dem, tex, cell_m, 7680, 4320, Camera())
    img = compose(rgb, cov, 3840, 2160)

Requires: numpy, scipy, Pillow.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import gaussian_filter

COL_BG = np.array([255, 255, 255], np.float32)
COL_CAST_SHADOW = np.array([186, 201, 228], np.float32)   # periwinkle ground shadow
COL_TITLE = (17, 17, 20)
COL_ACCENT = (74, 80, 195)
COL_CREDIT = (78, 78, 88)


@dataclass
class Camera:
    """Looking due north, tilted down. Tune zoom/pan to frame, not fov."""
    pitch_deg: float = 50.0        # steeper = more plan-readable, flatter = more drama
    dist_factor: float = 2.15      # eye distance in multiples of the N-S extent
    fov_deg: float = 36.0
    zoom: float = 1.50             # >1 lets the slab overfill the frame edges
    pan_x: float = 0.075           # fraction of frame width, + moves map right
    pan_y: float = 0.045           # fraction of frame height, + moves map down
    vert_exag: float = 13.0        # keep modest: the hillshade carries the drama
    geom_smooth_m: float = 2600.0  # smooth the height field before displacement
    skirt_dark: float = 0.45       # shading down a projected cliff face
    max_skirt_px: int = 420


def _project_bounds(dem, cell_m, out_w, out_h, cam, step=24):
    """Screen-space bbox of the projected slab at zoom=1, pan=0 (elevation included)."""
    h, w = dem.shape
    z = np.maximum(dem, 0.0).astype(np.float32) * cam.vert_exag
    rows = np.unique(np.r_[np.arange(0, h, step), h - 1])
    cols = np.unique(np.r_[np.arange(0, w, step), w - 1])
    half_w, half_h = (w - 1) * 0.5, (h - 1) * 0.5
    pitch = math.radians(cam.pitch_deg)
    dist = (h - 1) * cell_m * cam.dist_factor
    eye_y, eye_z = dist * math.sin(pitch), dist * math.cos(pitch)
    focal = (out_h * 0.5) / math.tan(math.radians(cam.fov_deg) * 0.5)
    sp, cp = math.sin(pitch), math.cos(pitch)
    wx = (cols - half_w) * cell_m
    xs, ys = [], []
    for r in rows:
        vy = z[r, cols] - eye_y
        vz = (r - half_h) * cell_m - eye_z
        cam_y = vy * cp - vz * sp
        cam_z = np.maximum(-(vy * sp + vz * cp), 1.0)
        xs.append(focal * wx / cam_z)
        ys.append(-focal * cam_y / cam_z)
    xs, ys = np.concatenate(xs), np.concatenate(ys)
    return xs.min(), xs.max(), ys.min(), ys.max(), focal


def autoframe(dem, cell_m, out_w, out_h, cam: Camera | None = None,
              target=(0.27, 0.06, 0.99, 0.94), verbose=True) -> Camera:
    """Fit the slab into a target rectangle of the frame, returning a tuned Camera.

    `target` is (left, top, right, bottom) as fractions of the frame. The default
    keeps the left quarter clear for the title block and legend — the single most
    common way these maps go wrong is a beautiful render with the caption sitting
    unreadably on top of the terrain.

    Region aspect ratios vary enormously (a wide mountain belt vs a squarish country),
    so hand-tuned zoom/pan almost never transfers. Fit, then nudge if you want.
    """
    import copy
    cam = copy.copy(cam or Camera())
    x0, x1, y0, y1, _ = _project_bounds(dem, cell_m, out_w, out_h, cam)
    tw = (target[2] - target[0]) * out_w
    th = (target[3] - target[1]) * out_h
    zoom = min(tw / max(x1 - x0, 1e-6), th / max(y1 - y0, 1e-6))
    # centre of the scaled slab, relative to frame centre
    cx_want = (target[0] + target[2]) * 0.5 * out_w
    cy_want = (target[1] + target[3]) * 0.5 * out_h
    cam.zoom = zoom
    cam.pan_x = (cx_want - (x0 + x1) * 0.5 * zoom - out_w * 0.5) / out_w
    cam.pan_y = (cy_want - (y0 + y1) * 0.5 * zoom - out_h * 0.5) / out_h
    if verbose:
        print(f" [+] autoframe: zoom={cam.zoom:.2f} pan=({cam.pan_x:+.3f},{cam.pan_y:+.3f})")
    return cam


def render(dem, tex, cell_m, out_w, out_h, cam: Camera | None = None,
           edge_taper=None, verbose=True):
    """Return (rgb uint8 [out_h,out_w,3], coverage uint8).

    The buffer starts white, not black: when a supersampled render is downsampled,
    silhouette pixels then blend terrain against the page instead of against black,
    which would otherwise ring as a dark hairline around the whole map.
    """
    cam = cam or Camera()
    h, w = dem.shape

    z = np.maximum(dem, 0.0).astype(np.float32)      # sea renders as a flat plane
    if cam.geom_smooth_m > 0:
        z = gaussian_filter(z, max(0.8, cam.geom_smooth_m / cell_m))
    if edge_taper is not None:
        z = z * (1.0 - edge_taper ** 0.9)            # slab edges slope away, no cliffs
    z *= cam.vert_exag

    half_w, half_h = (w - 1) * 0.5, (h - 1) * 0.5
    extent_ns = (h - 1) * cell_m
    pitch = math.radians(cam.pitch_deg)
    dist = extent_ns * cam.dist_factor
    eye_y, eye_z = dist * math.sin(pitch), dist * math.cos(pitch)
    focal = (out_h * 0.5) / math.tan(math.radians(cam.fov_deg) * 0.5) * cam.zoom
    sp, cp = math.sin(pitch), math.cos(pitch)
    cx = out_w * 0.5 + cam.pan_x * out_w
    cy = out_h * 0.5 + cam.pan_y * out_h

    wx = (np.arange(w, dtype=np.float32) - half_w) * cell_m
    rgb = np.full((out_h, out_w, 3), 255, np.uint8)
    cov = np.zeros((out_h, out_w), np.uint8)

    def project(row, elev_row):
        vy = elev_row - eye_y
        vz = (row - half_h) * cell_m - eye_z
        cam_y = vy * cp - vz * sp
        cam_z = np.maximum(-(vy * sp + vz * cp), 1.0)
        return cx + focal * wx / cam_z, cy - focal * cam_y / cam_z

    if verbose:
        print(f" [*] rasterising {w}x{h} -> {out_w}x{out_h} ...")
    t0 = time.time()
    sx_cur, sy_cur = project(0, z[0])
    for r in range(h):
        if r + 1 < h:
            sx_nxt, sy_nxt = project(r + 1, z[r + 1])
        else:
            sx_nxt, sy_nxt = sx_cur, sy_cur + 2.0

        xi = np.rint(sx_cur).astype(np.int32)
        y_top = np.rint(sy_cur).astype(np.int32)
        y_bot = np.rint(np.maximum(sy_nxt, sy_cur + 1.0)).astype(np.int32)
        valid = (xi >= 0) & (xi < out_w) & (y_bot > 0) & (y_top < out_h)
        if np.any(valid):
            span = np.clip(y_bot - y_top, 1, cam.max_skirt_px)
            kmax = int(span[valid].max())
            row_col = tex[r]
            deep = kmax > 3
            for k in range(kmax):
                yy = y_top + k
                m = valid & (k < span) & (yy >= 0) & (yy < out_h)
                if not np.any(m):
                    continue
                ym, xm = yy[m], xi[m]
                cm = row_col[m]
                if deep and k:
                    f = 1.0 - cam.skirt_dark * np.clip(k / np.maximum(span[m], 1), 0, 1)
                    cm = (cm.astype(np.float32) * f[:, None]).astype(np.uint8)
                rgb[ym, xm] = cm
                cov[ym, xm] = 255
                xm2 = np.minimum(xm + 1, out_w - 1)   # closes magnification gaps
                rgb[ym, xm2] = cm
                cov[ym, xm2] = 255
        sx_cur, sy_cur = sx_nxt, sy_nxt

    if verbose:
        print(f" [+] rasterised in {time.time() - t0:.0f}s")
    return rgb, cov


def compose(rgb, cov, out_w, out_h, shadow_strength=0.55, verbose=True):
    """Place the render on a white page with a soft directional cast shadow."""
    if rgb.shape[0] != out_h or rgb.shape[1] != out_w:
        rgb = np.asarray(Image.fromarray(rgb).resize((out_w, out_h), Image.LANCZOS))
        cov = np.asarray(Image.fromarray(cov).resize((out_w, out_h), Image.LANCZOS))
    if verbose:
        print(" [*] compositing shadow and page ...")

    scale = out_w / 3840.0
    canvas = np.ones((out_h, out_w, 3), np.float32) * COL_BG[None, None, :]
    m = cov.astype(np.float32) / 255.0          # soft coverage keeps edges anti-aliased

    shadow = np.zeros_like(m)
    steps = 26
    for i in range(1, steps + 1):
        dx, dy = int(round(i * 3.4 * scale)), int(round(i * 2.0 * scale))
        sh = np.zeros_like(m)
        sh[dy:, dx:] = m[:m.shape[0] - dy if dy else None,
                         :m.shape[1] - dx if dx else None]
        shadow += sh * (1.0 - i / (steps + 1.0))
    shadow = gaussian_filter(shadow / max(shadow.max(), 1e-6), 9.0 * scale)
    shadow = np.clip(shadow * 1.55, 0.0, 1.0) * shadow_strength * (1.0 - m)
    canvas += (COL_CAST_SHADOW[None, None, :] - canvas) * shadow[..., None]

    mm = m[..., None]
    canvas = canvas * (1.0 - mm) + rgb.astype(np.float32) * mm
    return Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB")


# --------------------------------------------------------------------- typography

def get_font(bold: bool, size: int):
    names = (["georgiab.ttf", "cambriab.ttf", "DejaVuSerif-Bold.ttf", "timesbd.ttf"]
             if bold else
             ["georgia.ttf", "cambria.ttc", "DejaVuSerif.ttf", "times.ttf"])
    dirs = [r"C:\Windows\Fonts", os.path.expanduser(r"~\AppData\Local\Microsoft\Windows\Fonts"),
            "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype",
            "/System/Library/Fonts/Supplemental", "/Library/Fonts"]
    for n in names:
        for d in dirs:
            p = os.path.join(d, n)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
    return ImageFont.load_default()


def draw_title(img, lines, country, scale=1.0, x=118, y=190):
    """Serif title block: caption lines in ink, then the subject in accent colour."""
    d = ImageDraw.Draw(img)
    f_head, f_country = get_font(False, int(58 * scale)), get_font(True, int(156 * scale))
    x, y = int(x * scale), int(y * scale)
    for i, ln in enumerate(lines):
        d.text((x, y + i * int(74 * scale)), ln, font=f_head, fill=COL_TITLE)
    d.text((x - int(6 * scale), y + int(168 * scale)), country,
           font=f_country, fill=COL_ACCENT)
    return d


def draw_legend(d, x, y, scale, ramp, title, ticks, note):
    """Horizontal ramp legend. Always say what the colour *means* in the note —
    a ramp without a stated variable is the most common way these maps mislead."""
    f_lab, f_tick = get_font(True, int(25 * scale)), get_font(False, int(23 * scale))
    f_note = get_font(False, int(22 * scale))
    bar_w, bar_h = int(430 * scale), int(20 * scale)
    d.text((x, y), title, font=f_lab, fill=COL_TITLE)
    top = y + int(38 * scale)
    xs = [p for p, _ in ramp]
    for i in range(bar_w):
        t = i / float(bar_w - 1)
        col = tuple(int(np.interp(t, xs, [c[k] for _, c in ramp])) for k in range(3))
        d.line([(x + i, top), (x + i, top + bar_h)], fill=col)
    d.rectangle([x, top, x + bar_w, top + bar_h], outline=(120, 120, 132), width=1)
    for frac, label in ticks:
        tx = x + int(frac * bar_w)
        d.line([(tx, top + bar_h), (tx, top + bar_h + int(6 * scale))],
               fill=(120, 120, 132), width=1)
        tw = d.textlength(label, font=f_tick)
        d.text((tx - tw / 2, top + bar_h + int(10 * scale)), label,
               font=f_tick, fill=COL_CREDIT)
    ny = top + bar_h + int(42 * scale)
    for ln in note.split("\n"):
        d.text((x, ny), ln, font=f_note, fill=COL_CREDIT)
        ny += int(28 * scale)
    return ny


def draw_bivariate_legend(img, x, y, scale, corners, x_label, y_label,
                          x_ticks, y_ticks, note="", size=300, gamma=1.0,
                          corner_notes=None):
    """2-D square legend for a bivariate layer, drawn onto a PIL image.

    `corners` matches shading.bivariate_rgb: (low-low, high-low, low-high, high-high).
    `x_ticks`/`y_ticks` are (fraction, label) pairs; y fractions run bottom-up.

    Bivariate maps fail when the reader cannot decode the colour, so this draws the
    full 2-D field rather than two separate bars, and `corner_notes` lets you name the
    four extremes in words — which is what most readers actually use.
    """
    d = ImageDraw.Draw(img)
    f_lab = get_font(True, int(25 * scale))
    f_tick = get_font(False, int(22 * scale))
    f_note = get_font(False, int(22 * scale))
    s = int(size * scale)

    c00, c10, c01, c11 = [np.asarray(c, np.float32) for c in corners]
    gx = (np.linspace(0, 1, s, dtype=np.float32) ** gamma)[None, :, None]
    gy = (np.linspace(1, 0, s, dtype=np.float32) ** gamma)[:, None, None]   # top = wet
    top = c00 * (1 - gx) + c10 * gx
    bot = c01 * (1 - gx) + c11 * gx
    block = (top * (1 - gy) + bot * gy).astype(np.uint8)
    img.paste(Image.fromarray(block, "RGB"), (x, y))
    d.rectangle([x, y, x + s - 1, y + s - 1], outline=(120, 120, 132), width=1)

    for frac, label in x_ticks:
        tx = x + int(frac * (s - 1))
        d.line([(tx, y + s), (tx, y + s + int(6 * scale))], fill=(120, 120, 132), width=1)
        tw = d.textlength(label, font=f_tick)
        d.text((tx - tw / 2, y + s + int(10 * scale)), label, font=f_tick, fill=COL_CREDIT)
    for frac, label in y_ticks:
        ty = y + int((1.0 - frac) * (s - 1))
        d.line([(x - int(6 * scale), ty), (x, ty)], fill=(120, 120, 132), width=1)
        tw = d.textlength(label, font=f_tick)
        d.text((x - int(12 * scale) - tw, ty - int(11 * scale)), label,
               font=f_tick, fill=COL_CREDIT)

    d.text((x, y + s + int(46 * scale)), x_label, font=f_lab, fill=COL_TITLE)
    # rotated y-axis label
    tw = int(d.textlength(y_label, font=f_lab))
    strip = Image.new("RGBA", (tw + 8, int(34 * scale)), (255, 255, 255, 0))
    ImageDraw.Draw(strip).text((0, 0), y_label, font=f_lab, fill=COL_TITLE + (255,))
    strip = strip.rotate(90, expand=True)
    img.paste(strip, (x - int(150 * scale), y + (s - strip.height) // 2), strip)

    ny = y + s + int(86 * scale)
    if corner_notes:
        for cn, col in corner_notes:
            d.rectangle([x, ny + int(4 * scale), x + int(17 * scale), ny + int(21 * scale)],
                        fill=tuple(int(v) for v in col), outline=(150, 150, 160))
            d.text((x + int(26 * scale), ny), cn, font=f_note, fill=COL_CREDIT)
            ny += int(28 * scale)
        ny += int(6 * scale)
    for ln in note.split("\n") if note else []:
        d.text((x, ny), ln, font=f_note, fill=COL_CREDIT)
        ny += int(28 * scale)
    return ny


def draw_credits(d, lines, out_w, out_h, scale=1.0, x=118):
    f = get_font(False, int(25 * scale))
    cy = out_h - int(168 * scale)
    for i, ln in enumerate(lines):
        d.text((int(x * scale), cy + i * int(34 * scale)), ln, font=f, fill=COL_CREDIT)
