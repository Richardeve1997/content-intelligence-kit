"""Snap an image model's "pixel art" onto a true pixel grid with a small palette.

An image model draws pixel art with soft edges, pixels of uneven size and a few stray colours. snap()
finds the grid the picture was drawn on (or takes the pitch it is given), gives every cell of that grid
one flat colour, makes the background clear and writes one image pixel per art pixel.

How a picture is snapped:

1. The grid. Colour changes line up on the pixel boundaries, so the pitch and phase with the most edge
   energy on their grid lines win (an edge-energy comb). With a pitch given, only the phase is found.
2. A colour per cell: the median of the inner part of the cell, away from the soft edges.
3. The background. When the picture has its own transparency, its alpha decides: a cell at least half
   opaque is figure. Otherwise a cell close to the background colour and joined to the border is
   background, and so is a hole in the figure too big to be an eye white.
4. The palette: k-means over the figure's cells, with entries that noise split from one colour merged
   again, or a locked palette that is used as it is. A small light patch inside the figure (an eye white
   or teeth, which sit close to a light background) gets its own entry when nothing in the palette is near it.
5. Every cell takes the most common palette colour among its raw pixels. Thin dark outlines and small
   light patches win a cell with less than a majority, so they survive.
6. Clean-up: specks on the background, lone noisy cells, a pale fringe around the figure, and small holes
   inside the figure. A small hole is filled; a bigger one (between an arm and the body) stays clear.

The array functions do the work and snap() is the file-to-file wrapper.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage.color import lab2rgb, rgb2lab
from sklearn.cluster import KMeans

from kit.config import KitError

FLATTEN_ON = (230, 230, 230)  # a picture with transparency is laid on this neutral grey first
BACKGROUND_DE = 6.0  # a cell this close to the background colour (Lab distance) counts as background
OUTLINE_L = 16.0  # palette entries darker than this lightness are outline ink
OUTLINE_BIAS = 0.30  # an ink share this large makes a cell ink, so one-pixel outlines survive
LIGHT_BIAS = 0.30  # the same for small light patches (eye whites, teeth)
PINHOLE = 10  # enclosed holes smaller than this many cells are filled, on a grid 140 cells tall; it grows with
# the area of the grid, so an eye white on a finer grid is still filled while the gap under an arm stays clear
MERGE_DE = 4.0  # palette entries closer than this are one colour split by noise
SEED = 7

__all__ = [
    "SnapError", "Grid", "Snapped", "Slot", "Figure", "load_picture", "load_rgb", "background_colour", "detect_grid",
    "phase_for", "cell_edges", "snap_array", "snap", "split_figures", "figures",
]  # fmt: skip


class SnapError(KitError):
    """A picture could not be snapped. The message says what is wrong with it in plain words."""


# --- reading a picture ---------------------------------------------------------------------------------------


def load_picture(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """(the picture as float RGB in 0..1, which pixels are opaque, or None for a picture with no transparency).

    A picture with transparency is laid on a neutral grey with its alpha made hard first: a pixel at least half
    opaque keeps its own colour and the rest becomes the grey. Blending the soft edge instead would leave a
    fringe of in-between colours around the figure. Its alpha also says exactly what is background, so the
    snap uses that rather than guessing from colour."""
    try:
        with Image.open(path) as image:
            image.load()
            if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
                rgba = np.asarray(image.convert("RGBA"))
                solid = rgba[..., 3] >= 128
                if solid.all():
                    return rgba[..., :3].astype(np.float64) / 255.0, None
                out = np.empty(rgba.shape[:2] + (3,), np.uint8)
                out[:] = FLATTEN_ON
                out[solid] = rgba[..., :3][solid]
                return out.astype(np.float64) / 255.0, solid
            return np.asarray(image.convert("RGB"), dtype=np.float64) / 255.0, None
    except OSError:
        raise SnapError(f"{Path(path).name} is not a picture this kit can open.") from None


def load_rgb(path: Path) -> np.ndarray:
    """The picture as float RGB in 0..1, with any transparency laid on a neutral grey (see load_picture)."""
    return load_picture(path)[0]


def background_colour(rgb: np.ndarray) -> np.ndarray:
    """The median colour of a six-pixel border all round, which the prompts ask to be one flat colour."""
    k = max(1, min(6, rgb.shape[0] // 4, rgb.shape[1] // 4))
    border = np.concatenate(
        [rgb[:k].reshape(-1, 3), rgb[-k:].reshape(-1, 3), rgb[:, :k].reshape(-1, 3), rgb[:, -k:].reshape(-1, 3)]
    )
    return np.median(border, axis=0)


# --- the grid ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Grid:
    pitch: float  # raw pixels per art pixel
    phase_x: float  # where the first cell edge falls, in raw pixels
    phase_y: float
    score: float = 0.0


def _edge_profiles(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lab = rgb2lab(rgb)
    ex = np.abs(np.diff(lab, axis=1)).sum(2).sum(0)  # entry b is the boundary between columns b and b + 1
    ey = np.abs(np.diff(lab, axis=0)).sum(2).sum(1)
    return ex, ey


def _comb(e: np.ndarray, pitch: float, phase: float) -> float:
    teeth = np.round(np.arange(phase, len(e), pitch)).astype(int)
    teeth = teeth[(teeth >= 0) & (teeth < len(e))]
    if not len(teeth):
        return 0.0
    return float(e[teeth].mean() / (e.mean() + 1e-9))


def _best_phase(e: np.ndarray, pitch: float) -> tuple[float, float]:
    phases = np.arange(0.0, pitch, 0.25)
    scores = [_comb(e, pitch, ph) for ph in phases]
    best = int(np.argmax(scores))
    return float(phases[best]), float(scores[best])


def detect_grid(rgb: np.ndarray, lo: float = 2.0, hi: float = 12.0) -> Grid:
    """The pitch and phase of the pixel grid a picture was drawn on, searched from lo to hi raw pixels.

    Whole multiples of the true pitch score almost as well as the pitch itself, so the search takes the
    smallest pitch that scores within 92% of the best one, then the best pitch just above it (the scores
    around a true pitch form a small hill, and its foot also clears 92%)."""
    ex, ey = _edge_profiles(rgb)
    found = []
    for pitch in np.arange(lo, hi, 0.02):
        sx = max(_comb(ex, pitch, ph) for ph in np.arange(0.0, pitch, 0.25))
        sy = max(_comb(ey, pitch, ph) for ph in np.arange(0.0, pitch, 0.25))
        found.append((float(pitch), (sx + sy) / 2))
    top = max(score for _, score in found)
    smallest = min(p for p, score in found if score >= 0.92 * top)
    hill = [(p, score) for p, score in found if smallest <= p <= smallest * 1.08]
    pitch = round(max(hill, key=lambda item: item[1])[0], 2)
    phx, sx = _best_phase(ex, pitch)
    phy, sy = _best_phase(ey, pitch)
    return Grid(pitch, phx + 1.0, phy + 1.0, round((sx + sy) / 2, 3))


def phase_for(rgb: np.ndarray, pitch: float) -> tuple[float, float]:
    """The phase of a grid whose pitch is already known."""
    ex, ey = _edge_profiles(rgb)
    return _best_phase(ex, pitch)[0] + 1.0, _best_phase(ey, pitch)[0] + 1.0


def cell_edges(n_px: int, pitch: float, phase: float) -> np.ndarray:
    """The raw pixel positions where cells start along one axis, ending with n_px."""
    start = phase % pitch
    edges = np.arange(start, n_px + pitch, pitch)
    if start > 0.5:
        edges = np.concatenate([[0.0], edges])
    return np.unique(np.clip(np.round(edges).astype(int), 0, n_px))


def _pixel_cells(n_px: int, edges: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For every pixel along one axis: its cell, and whether it lies in the inner part of that cell."""
    pixels = np.arange(n_px)
    cell = np.clip(np.searchsorted(edges, pixels, side="right") - 1, 0, len(edges) - 2)
    start, stop = edges[cell], edges[cell + 1]
    inset = np.round((stop - start) * 0.2).astype(int)
    inner = (pixels >= start + inset) & (pixels < np.maximum(start + inset + 1, stop - inset))
    return cell, inner


# --- snapping --------------------------------------------------------------------------------------------------


@dataclass
class Snapped:
    """A picture on its art grid: a palette index per cell (0 is the background) and the palette."""

    idx: np.ndarray
    palette: np.ndarray  # (K, 3) uint8; entry 0 is the background colour found in the picture
    pitch: float
    phase: tuple[float, float]
    x_edges: np.ndarray = field(repr=False)
    y_edges: np.ndarray = field(repr=False)

    def rgba(self) -> np.ndarray:
        out = np.zeros(self.idx.shape + (4,), np.uint8)
        solid = self.idx != 0
        out[..., :3][solid] = self.palette[self.idx[solid]]
        out[..., 3][solid] = 255
        return out

    def used_colours(self) -> list[tuple[int, int, int]]:
        """The palette entries the figure actually uses, in palette order, background left out."""
        present = np.unique(self.idx[self.idx != 0])
        return [tuple(int(v) for v in self.palette[k]) for k in present]

    def to_art_x(self, raw_x: float) -> int:
        return int(np.clip(np.searchsorted(self.x_edges, raw_x, side="right") - 1, 0, len(self.x_edges) - 2))

    def to_art_y(self, raw_y: float) -> int:
        return int(np.clip(np.searchsorted(self.y_edges, raw_y, side="right") - 1, 0, len(self.y_edges) - 2))


def _neighbours(idx: np.ndarray, fill: int | None = None) -> np.ndarray:
    """The 8 neighbours of every cell, stacked on the first axis. Edges repeat, or take `fill`."""
    h, w = idx.shape
    pad = np.pad(idx, 1, mode="edge") if fill is None else np.pad(idx, 1, mode="constant", constant_values=fill)
    return np.stack(
        [pad[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w] for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx], 0
    )


def _denoise(idx: np.ndarray, pal: np.ndarray, need: int = 6, de: float = 14.0) -> np.ndarray:
    """A lone cell whose neighbours are mostly one close colour takes that colour. Contrasting dots stay."""
    h, w = idx.shape
    neigh = _neighbours(idx)
    counts = np.stack([(neigh == k).sum(0) for k in range(len(pal))], 0)
    majority, most = counts.argmax(0), counts.max(0)
    same = counts[idx, np.arange(h)[:, None], np.arange(w)[None]]
    close = np.linalg.norm(pal[idx] - pal[majority], axis=2) < de
    change = (same == 0) & (most >= need) & close & (idx != 0) & (majority != 0)
    out = idx.copy()
    out[change] = majority[change]
    return out


def _dehalo(idx: np.ndarray, pal: np.ndarray, de_bg: float = 25.0, de_same: float = 12.0) -> np.ndarray:
    """A pale cell on the edge of a figure, near the background colour and with few cells like it, goes."""
    neigh = _neighbours(idx, fill=0)
    background_neighbours = (neigh == 0).sum(0)
    near_bg = np.linalg.norm(pal - pal[0], axis=1) < de_bg
    alike = np.linalg.norm(pal[:, None] - pal[None], axis=2) < de_same
    similar = (alike[idx[None], neigh] & (neigh != 0)).sum(0)
    halo = (idx != 0) & (background_neighbours >= 3) & near_bg[idx] & (similar <= 1)
    out = idx.copy()
    out[halo] = 0
    return out


def _pockets(idx: np.ndarray) -> tuple[np.ndarray, int, np.ndarray, set[int]]:
    """Background regions: (labels, count, sizes, the labels that touch the border)."""
    labels, count = ndimage.label(idx == 0)
    sizes = ndimage.sum(np.ones_like(labels), labels, index=np.arange(1, count + 1)) if count else np.zeros(0)
    border = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))) - {0}
    return labels, count, sizes, border


def _merge_close(centres: np.ndarray, weights: np.ndarray, within: float = MERGE_DE) -> np.ndarray:
    """Merge palette entries closer than `within` (Lab distance) into their weighted mean. Noise in a picture
    can split one flat colour into several entries that no eye could tell apart."""
    centres, weights = [c for c in centres], [w for w in weights]
    while len(centres) > 1:
        stack = np.array(centres)
        gaps = np.linalg.norm(stack[:, None] - stack[None], axis=2) + np.eye(len(centres)) * 1e9
        a, b = np.unravel_index(int(np.argmin(gaps)), gaps.shape)
        if gaps[a, b] >= within:
            break
        total = weights[a] + weights[b]
        merged = (centres[a] * weights[a] + centres[b] * weights[b]) / max(total, 1e-9)
        for i in sorted((a, b), reverse=True):
            del centres[i], weights[i]
        centres.append(merged)
        weights.append(total)
    return np.array(centres)


def _palette(med: np.ndarray, candidates: np.ndarray, bg_lab: np.ndarray, colours: int) -> np.ndarray:
    """k-means palette (Lab) for the figure cells, with the background as entry 0."""
    data = med[candidates]
    if not len(data):
        raise SnapError("The picture looks empty: no figure stands out from the background.")
    distinct = len(np.unique(np.round(data, 1), axis=0))
    k = max(1, min(int(colours), distinct))
    fitted = KMeans(n_clusters=k, n_init=4, random_state=SEED).fit(data)
    centres = _merge_close(fitted.cluster_centers_, np.bincount(fitted.labels_, minlength=k).astype(np.float64))
    k = len(centres)
    # a small light patch (eye whites, teeth) is easily merged into a bigger cluster or into the background;
    # when nothing in the palette is near it, it gets an entry of its own and the two closest entries merge
    light = data[(data[:, 0] >= max(bg_lab[0] - 4.0, 80.0)) & (np.linalg.norm(data - bg_lab, axis=1) >= 3.5)]
    if len(light) >= 2 and k >= 2:
        patch = np.median(light, axis=0)
        if np.linalg.norm(centres - patch, axis=1).min() > 6.0:
            if k >= colours:
                gaps = np.linalg.norm(centres[:, None] - centres[None], axis=2) + np.eye(k) * 1e9
                a, b = np.unravel_index(int(np.argmin(gaps)), gaps.shape)
                merged = (centres[a] + centres[b]) / 2
                centres = np.vstack([np.delete(centres, [a, b], axis=0), merged[None]])
            centres = np.vstack([centres, patch[None]])
    return np.vstack([bg_lab[None], centres])


def snap_array(
    rgb: np.ndarray,
    pitch: float,
    phase_x: float,
    phase_y: float,
    *,
    colours: int,
    palette: Sequence[Sequence[int]] | None = None,
    opaque: np.ndarray | None = None,
) -> Snapped:
    """Snap a float RGB picture onto the grid given by pitch and phase. See the module notes for the steps.

    `opaque` (one bool per raw pixel) comes with a picture that has its own transparency. A cell that is at
    least half opaque is then figure and the rest background, whatever their colours."""
    if pitch < 1.0:
        raise SnapError(f"A pitch of {pitch} raw pixels per art pixel is too small to snap.")
    h_px, w_px, _ = rgb.shape
    xs, ys = cell_edges(w_px, pitch, phase_x), cell_edges(h_px, pitch, phase_y)
    nx, ny = len(xs) - 1, len(ys) - 1
    jx, inner_x = _pixel_cells(w_px, xs)
    jy, inner_y = _pixel_cells(h_px, ys)
    cell = jy[:, None] * nx + jx[None, :]
    lab = rgb2lab(rgb)

    # 1. a median colour per cell from its inner pixels
    labels = np.where(inner_y[:, None] & inner_x[None, :], cell + 1, 0)
    index = np.arange(1, nx * ny + 1)
    med = np.stack([np.asarray(ndimage.median(lab[..., c], labels=labels, index=index)) for c in range(3)], axis=-1)
    med = med.reshape(ny, nx, 3)

    # 2. which cells are background: from the picture's own transparency when it has one; otherwise close to the
    # background colour and joined to the border, or a hole too big to be an eye white
    pinhole = max(PINHOLE, round(PINHOLE * (ny / 140) ** 2))
    bg_rgb = background_colour(rgb) if opaque is None else np.array(FLATTEN_ON, np.float64) / 255.0
    bg_lab = rgb2lab(bg_rgb[None, None])[0, 0]
    if opaque is not None:
        share = np.bincount(cell.ravel(), weights=opaque.ravel().astype(np.float64), minlength=nx * ny)
        share /= np.maximum(np.bincount(cell.ravel(), minlength=nx * ny), 1)
        background = share.reshape(ny, nx) < 0.5
    else:
        bgish = np.linalg.norm(med - bg_lab, axis=2) <= BACKGROUND_DE
        regions, count = ndimage.label(bgish)
        sizes = ndimage.sum(np.ones_like(regions), regions, index=np.arange(1, count + 1)) if count else np.zeros(0)
        border = set(np.unique(np.concatenate([regions[0], regions[-1], regions[:, 0], regions[:, -1]]))) - {0}
        keep_clear = [k for k in range(1, count + 1) if k in border or sizes[k - 1] >= pinhole]
        background = np.isin(regions, keep_clear)
    candidates = ~background

    # 3. the palette
    if palette is not None:
        fixed = np.asarray(palette, dtype=np.float64).reshape(-1, 3)
        if not len(fixed):
            raise SnapError("The locked palette is empty.")
        pal = np.vstack([bg_lab[None], rgb2lab(fixed[None] / 255.0)[0]])
    else:
        if int(colours) < 1:
            raise SnapError("Ask for at least one colour.")
        pal = _palette(med, candidates, bg_lab, colours)
    k_entries = len(pal)

    # 4. every candidate cell takes the commonest palette colour among its raw pixels. When the picture's own
    # transparency marks the figure, no pixel of it can vote for the background.
    if opaque is not None:
        near = cKDTree(pal[1:]).query(lab.reshape(-1, 3))[1] + 1
    else:
        near = cKDTree(pal).query(lab.reshape(-1, 3))[1]
    cand_ids = np.flatnonzero(candidates.ravel())
    compact = np.full(nx * ny, -1)
    compact[cand_ids] = np.arange(len(cand_ids))
    owner = compact[cell.ravel()]
    mine = owner >= 0
    counts = np.bincount(owner[mine] * k_entries + near[mine], minlength=len(cand_ids) * k_entries)
    counts = counts.reshape(len(cand_ids), k_entries)
    total = counts.sum(1)
    order = np.argsort(-counts, axis=1, kind="stable")
    first, second = order[:, 0], order[:, 1] if k_entries > 1 else order[:, 0]
    rows = np.arange(len(cand_ids))
    cell_med = med.reshape(-1, 3)[cand_ids]
    d_first = np.linalg.norm(pal[first] - cell_med, axis=1)
    d_second = np.linalg.norm(pal[second] - cell_med, axis=1)
    clear_win = counts[rows, first] >= 1.4 * counts[rows, second]
    choice = np.where(clear_win | (d_first <= d_second), first, second)
    dark = [k for k in range(1, k_entries) if pal[k, 0] < OUTLINE_L]
    inked = np.zeros(len(cand_ids), bool)
    if dark:
        dark_counts = counts[:, dark]
        inked = dark_counts.sum(1) >= OUTLINE_BIAS * total
        choice[inked] = np.asarray(dark)[np.argmax(dark_counts[inked], axis=1)]
    light = [k for k in range(1, k_entries) if pal[k, 0] >= max(bg_lab[0] - 4.0, 80.0)]
    if light:
        light_counts = counts[:, light]
        lit = (light_counts.sum(1) >= LIGHT_BIAS * total) & (counts[:, 0] < 0.2 * total) & ~inked
        choice[lit] = np.asarray(light)[np.argmax(light_counts[lit], axis=1)]
    idx = np.zeros(nx * ny, int)
    idx[cand_ids] = choice
    idx = idx.reshape(ny, nx)

    # 5. clean-up
    solid = idx != 0
    neighbours = ndimage.convolve(solid.astype(int), np.ones((3, 3), int), mode="constant") - solid
    idx[solid & (neighbours == 0)] = 0  # specks
    for _ in range(2):
        idx = _denoise(idx, pal)
    for _ in range(2):  # lone pale cells on the edge of the figure, left over from a soft edge
        idx = _dehalo(idx, pal)
    # small groups of pale cells on the edge go too, unless the picture's own transparency drew that edge
    pale = (np.linalg.norm(pal - pal[0], axis=1) < 25.0)[idx] & (idx != 0)
    groups, n_groups = ndimage.label(pale, structure=np.ones((3, 3))) if opaque is None else (None, 0)
    if n_groups:
        group_sizes = ndimage.sum(np.ones_like(groups), groups, index=np.arange(1, n_groups + 1))
        touching = ndimage.binary_dilation(idx == 0, structure=np.ones((3, 3)))
        for g in range(1, n_groups + 1):
            members = groups == g
            if group_sizes[g - 1] <= 6 and (members & touching).any():
                idx[members] = 0
    # small holes: a light hole is an eye white or teeth and takes the nearest light entry; any other small
    # hole takes the commonest colour around it
    holes, n_holes, hole_sizes, border_holes = _pockets(idx)
    for k in range(1, n_holes + 1):
        if k in border_holes or hole_sizes[k - 1] >= pinhole:
            continue
        hole = holes == k
        colour = np.median(med[hole], axis=0)
        if light and colour[0] >= bg_lab[0] - 4.0 and np.linalg.norm(colour - bg_lab) >= 3.5:
            idx[hole] = min(light, key=lambda e: np.linalg.norm(pal[e] - colour))
            continue
        ring = ndimage.binary_dilation(hole) & ~hole
        around = idx[ring]
        around = around[around != 0]
        if len(around):
            idx[hole] = np.bincount(around).argmax()

    if palette is not None:
        pal_rgb = np.vstack([np.round(bg_rgb * 255), np.asarray(palette, dtype=np.float64).reshape(-1, 3)])
    else:
        pal_rgb = np.clip(lab2rgb(pal[None])[0] * 255.0, 0, 255).round()
        pal_rgb[0] = np.round(bg_rgb * 255)
    return Snapped(idx, pal_rgb.astype(np.uint8), float(pitch), (float(phase_x), float(phase_y)), xs, ys)


def snap(
    raw: Path,
    out: Path,
    *,
    colours: int,
    pitch: float | None = None,
    palette: Sequence[Sequence[int]] | None = None,
) -> Path:
    """Snap the picture at `raw` and write it to `out` as an RGBA PNG, one image pixel per art pixel.

    `colours` caps the opaque colours; with `palette` (a list of RGB colours) every cell takes one of those
    instead. Without `pitch` the grid is found in the picture. Beside the PNG, out.json records the pitch,
    the phase, the size and the colours used."""
    rgb, opaque = load_picture(Path(raw))
    if pitch is None:
        grid = detect_grid(rgb)
        pitch_used, phx, phy = grid.pitch, grid.phase_x, grid.phase_y
    else:
        pitch_used = float(pitch)
        phx, phy = phase_for(rgb, pitch_used)
    snapped = snap_array(rgb, pitch_used, phx, phy, colours=colours, palette=palette, opaque=opaque)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(snapped.rgba(), "RGBA").save(out)
    meta = {
        "pitch": round(pitch_used, 3),
        "phase": [round(phx, 2), round(phy, 2)],
        "size": [int(snapped.idx.shape[1]), int(snapped.idx.shape[0])],
        "palette": ["#" + "".join(f"{v:02x}" for v in c) for c in snapped.used_colours()],
        "background": "#" + "".join(f"{int(v):02x}" for v in snapped.palette[0]),
    }
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return out


# --- finding the figures in a sheet --------------------------------------------------------------------------------


@dataclass
class Slot:
    """Where one figure sits in a raw picture, in raw pixels (x1 and y1 one past the end)."""

    x0: int
    x1: int
    y0: int
    y1: int
    props: list[tuple[int, int, int, int]] = field(default_factory=list)
    split_right: bool = False  # this figure touched the next one and was cut from it

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def width(self) -> int:
        return self.x1 - self.x0


def split_figures(
    rgb: np.ndarray, *, expect: int | None = None, min_height: float = 0.2, opaque: np.ndarray | None = None
) -> list[Slot]:
    """The figures in a raw sheet or strip, left to right.

    A figure is a connected shape taller than `min_height` of the picture: its opaque pixels when the picture
    has its own transparency (`opaque`), else the pixels that differ from the background colour. Smaller
    shapes (sparks, sweat drops) go with the figure they sit over. With `expect`, while fewer figures are
    found than expected the widest one is taken to be two touching figures and cut at its emptiest column in
    the middle half."""
    if opaque is not None:
        fg = np.asarray(opaque, bool)
    else:
        arr = np.asarray(rgb, dtype=np.float64)
        if arr.max() <= 1.0:
            arr = arr * 255.0
        bg = np.median(
            np.concatenate([arr[:6].reshape(-1, 3), arr[-6:].reshape(-1, 3), arr[:, :6].reshape(-1, 3), arr[:, -6:].reshape(-1, 3)]),
            axis=0,
        )
        fg = np.linalg.norm(arr - bg, axis=2) > 18
    labels, _ = ndimage.label(ndimage.binary_dilation(fg, iterations=2))
    shapes = [(k + 1, s) for k, s in enumerate(ndimage.find_objects(labels)) if s is not None]
    tall = max(8, int(min_height * fg.shape[0]))
    big = sorted([(k, s) for k, s in shapes if s[0].stop - s[0].start > tall], key=lambda t: t[1][1].start)
    slots = []
    for k, s in big:
        ys, xs = np.nonzero(fg[s] & (labels[s] == k))
        slots.append(Slot(s[1].start + int(xs.min()), s[1].start + int(xs.max()) + 1,
                          s[0].start + int(ys.min()), s[0].start + int(ys.max()) + 1))  # fmt: skip
    while expect and slots and len(slots) < expect:
        i = max(range(len(slots)), key=lambda j: slots[j].width)
        wide = slots[i]
        profile = fg[wide.y0 : wide.y1, wide.x0 : wide.x1].sum(0)
        lo, hi = wide.width // 4, 3 * wide.width // 4
        if hi <= lo:
            break
        cut = wide.x0 + lo + int(np.argmin(profile[lo:hi]))
        parts = [Slot(wide.x0, cut, wide.y0, wide.y1, split_right=True), Slot(cut + 1, wide.x1, wide.y0, wide.y1)]
        for part in parts:
            rows = np.nonzero(fg[:, part.x0 : part.x1].any(1))[0]
            part.y0, part.y1 = int(rows.min()), int(rows.max()) + 1
        slots[i : i + 1] = parts
    for k, s in shapes:
        height, width = s[0].stop - s[0].start, s[1].stop - s[1].start
        if height > tall or height * width < 30 or not slots:
            continue
        cx = (s[1].start + s[1].stop) / 2
        inside = [i for i, b in enumerate(slots) if b.x0 - 40 <= cx <= b.x1 + 40]
        i = inside[0] if inside else int(np.argmin([abs(cx - (b.x0 + b.x1) / 2) for b in slots]))
        slots[i].props.append((s[1].start, s[0].start, s[1].stop, s[0].stop))
    return slots


@dataclass
class Figure:
    """One figure cut from a snapped picture."""

    rgba: np.ndarray  # cropped to the figure's own cells
    x0: int  # its top-left on the snapped grid
    y0: int

    @property
    def bottom(self) -> int:
        return self.y0 + self.rgba.shape[0]

    @property
    def height(self) -> int:
        return self.rgba.shape[0]


def figures(snapped: Snapped, slots: list[Slot]) -> list[Figure]:
    """Cut each slot's figure out of a snapped picture. Every connected group of cells goes to the slot it
    sits over; a figure that was cut from its neighbour in the raw picture is cut at the same column here."""
    solid = snapped.idx != 0
    labels, count = ndimage.label(solid, structure=np.ones((3, 3)))
    sizes = ndimage.sum(np.ones_like(labels), labels, index=np.arange(1, count + 1)) if count else np.zeros(0)
    ranges = [(snapped.to_art_x(s.x0) - 2, snapped.to_art_x(s.x1) + 2) for s in slots]
    owner = np.full(count + 1, -1)
    for k, where in enumerate(ndimage.find_objects(labels), 1):
        if where is None or sizes[k - 1] < 3:
            continue
        cx = (where[1].start + where[1].stop) / 2
        inside = [i for i, (a, b) in enumerate(ranges) if a <= cx <= b]
        if inside:
            owner[k] = min(inside, key=lambda i: abs(cx - sum(ranges[i]) / 2))
        else:
            owner[k] = int(np.argmin([abs(cx - (a + b) / 2) for a, b in ranges]))
    cell_owner = np.where(solid, owner[labels], -1)
    columns = np.arange(cell_owner.shape[1])[None, :]
    for i, slot in enumerate(slots[:-1]):
        if slot.split_right:
            cut = snapped.to_art_x(slot.x1)
            both = (cell_owner == i) | (cell_owner == i + 1)
            cell_owner[both & (columns > cut)] = i + 1
            cell_owner[both & (columns <= cut)] = i
    rgba = snapped.rgba()
    found = []
    for i in range(len(slots)):
        mine = cell_owner == i
        if not mine.any():
            raise SnapError(f"Figure {i + 1} came out empty after snapping. Draw that picture again.")
        ys, xs = np.nonzero(mine)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        cut = rgba[y0:y1, x0:x1].copy()
        cut[~mine[y0:y1, x0:x1]] = 0
        found.append(Figure(cut, int(x0), int(y0)))
    return found
