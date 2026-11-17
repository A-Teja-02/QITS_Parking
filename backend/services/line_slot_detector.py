"""
Parking slot layout from painted floor lines (yellow / blue / white).

Pipeline:
  1. colour mask for the chosen paint colours (HSV), white via a thin-bright-structure filter
  2. drop blobs that are not line-shaped (e.g. a yellow car body, a blue pillar)
  3. Hough line segments, merged into long lines (handles thick paint and lines broken by cars)
  4. split lines into "dividers" (between bays) and "row edges" (front/back line of a row)
  5. group dividers into rows, then make one perspective-correct slot between each pair of
     neighbouring dividers; an over-wide gap (divider hidden by a car) is split evenly.

Works for the common CCTV view looking at the front of a row; if too few dividers are found
it also tries the rotated case (camera looking along the row).
"""
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

HSV_RANGES = {
    "yellow": [((12, 70, 90), (40, 255, 255))],
    "blue": [((88, 80, 50), (132, 255, 255))],
}


# ----------------------------------------------------------------------
# 1-2. Paint mask
# ----------------------------------------------------------------------
def paint_mask(frame: np.ndarray, colors: List[str]) -> np.ndarray:
    h, w = frame.shape[:2]
    blur = cv2.GaussianBlur(frame, (5, 5), 0)
    hsv = cv2.cvtColor(blur, cv2.COLOR_BGR2HSV)
    mask = np.zeros((h, w), np.uint8)
    for c in colors:
        for lo, hi in HSV_RANGES.get(c, []):
            mask |= cv2.inRange(hsv, np.array(lo), np.array(hi))
    if "white" in colors:
        v = hsv[:, :, 2]
        k = max(9, int(min(h, w) * 0.025)) | 1
        tophat = cv2.morphologyEx(v, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
        white = ((tophat > 35) & (hsv[:, :, 1] < 60) & (v > 150)).astype(np.uint8) * 255
        mask |= white
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    # Remove thick regions (a yellow car, a blue wall panel): an opening with a kernel wider
    # than any painted line keeps only the blobs, which are then cut out of the mask.
    k = max(15, int(0.02 * max(h, w))) | 1
    blobs = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    if cv2.countNonZero(blobs):
        mask = cv2.bitwise_and(mask, cv2.bitwise_not(cv2.dilate(blobs, np.ones((7, 7), np.uint8))))
    return _keep_line_like(mask)


def _keep_line_like(mask: np.ndarray) -> np.ndarray:
    """Removes compact blobs (car bodies, signs); keeps thin elongated paint."""
    h, w = mask.shape
    min_len = 0.05 * max(h, w)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = np.zeros_like(mask)
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        span = float(np.hypot(bw, bh))
        if span < min_len:
            continue
        comp = (labels[y:y + bh, x:x + bw] == i).astype(np.uint8)
        # average stroke thickness ~ area / skeleton length; approximate length by distance transform
        dist = cv2.distanceTransform(comp, cv2.DIST_L2, 3)
        thickness = 2.0 * float(dist.max())
        fill = area / float(max(1, bw * bh))
        line_like = thickness <= 0.04 * max(h, w) + 3 or fill < 0.35
        if line_like:
            out[y:y + bh, x:x + bw][comp > 0] = 255
    return out


# ----------------------------------------------------------------------
# 3. Lines
# ----------------------------------------------------------------------
def _segments(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    diag = float(np.hypot(h, w))
    segs = cv2.HoughLinesP(mask, 1, np.pi / 180, threshold=max(25, int(diag * 0.02)),
                           minLineLength=int(diag * 0.04), maxLineGap=int(diag * 0.025))
    return segs.reshape(-1, 4).astype(np.float64) if segs is not None else np.zeros((0, 4))


def _merge_segments(segs: np.ndarray, diag: float) -> List[Dict[str, Any]]:
    """Groups near-collinear segments (same angle, same offset) into single long lines."""
    items = []
    for x1, y1, x2, y2 in segs:
        ang = np.degrees(np.arctan2(y2 - y1, x2 - x1)) % 180.0
        t = np.radians(ang)
        nx, ny = -np.sin(t), np.cos(t)
        rho = x1 * nx + y1 * ny
        items.append({"ang": ang, "rho": rho, "pts": [(x1, y1), (x2, y2)], "len": float(np.hypot(x2 - x1, y2 - y1))})
    items.sort(key=lambda d: -d["len"])
    groups: List[Dict[str, Any]] = []
    rho_tol = max(6.0, diag * 0.012)
    for it in items:
        placed = False
        for g in groups:
            da = abs(it["ang"] - g["ang"])
            da = min(da, 180 - da)
            if da > 6.0:
                continue
            # offset of this segment's midpoint from the group's line
            t = np.radians(g["ang"])
            nx, ny = -np.sin(t), np.cos(t)
            mx = (it["pts"][0][0] + it["pts"][1][0]) / 2.0
            my = (it["pts"][0][1] + it["pts"][1][1]) / 2.0
            if abs(mx * nx + my * ny - g["rho"]) <= rho_tol:
                g["pts"] += it["pts"]
                g["len"] += it["len"]
                placed = True
                break
        if not placed:
            groups.append({"ang": it["ang"], "rho": it["rho"], "pts": list(it["pts"]), "len": it["len"]})

    lines = []
    for g in groups:
        pts = np.array(g["pts"], dtype=np.float32)
        vx, vy, x0, y0 = cv2.fitLine(pts, cv2.DIST_HUBER, 0, 0.01, 0.01).flatten()
        proj = (pts[:, 0] - x0) * vx + (pts[:, 1] - y0) * vy
        a, b = float(proj.min()), float(proj.max())
        p1 = (float(x0 + vx * a), float(y0 + vy * a))
        p2 = (float(x0 + vx * b), float(y0 + vy * b))
        length = float(np.hypot(p2[0] - p1[0], p2[1] - p1[1]))
        if length < diag * 0.06:
            continue
        ang = float(np.degrees(np.arctan2(p2[1] - p1[1], p2[0] - p1[0])) % 180.0)
        lines.append({"p1": p1, "p2": p2, "len": length, "support": g["len"], "ang": ang})
    return lines


# ----------------------------------------------------------------------
# 4-5. Slots
# ----------------------------------------------------------------------
def _intersect(p1, p2, q1, q2) -> Optional[Tuple[float, float]]:
    x1, y1 = p1; x2, y2 = p2; x3, y3 = q1; x4, y4 = q2
    d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(d) < 1e-9:
        return None
    a = x1 * y2 - y1 * x2
    b = x3 * y4 - y3 * x4
    return ((a * (x3 - x4) - (x1 - x2) * b) / d, (a * (y3 - y4) - (y1 - y2) * b) / d)


def _square_to_quad(q):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = q
    dx1, dx2, dy1, dy2 = x1 - x2, x3 - x2, y1 - y2, y3 - y2
    sx, sy = x0 - x1 + x2 - x3, y0 - y1 + y2 - y3
    g = h = 0.0
    if abs(sx) > 1e-9 or abs(sy) > 1e-9:
        den = dx1 * dy2 - dx2 * dy1 or 1e-9
        g = (sx * dy2 - dx2 * sy) / den
        h = (dx1 * sy - sx * dy1) / den
    a, b = x1 - x0 + g * x1, x3 - x0 + h * x3
    d, e = y1 - y0 + g * y1, y3 - y0 + h * y3

    def f(u, v):
        w = g * u + h * v + 1
        return ((a * u + b * v + x0) / w, (d * u + e * v + y0) / w)
    return f


def _boundary(points: List[Tuple[float, float]], extreme: str):
    """Line through divider end points (fitted slope), pushed out to the most extreme point."""
    pts = np.array(points, dtype=np.float64)
    if len(pts) >= 2 and np.ptp(pts[:, 0]) > 1:
        slope, icpt = np.polyfit(pts[:, 0], pts[:, 1], 1)
        resid = pts[:, 1] - (slope * pts[:, 0] + icpt)
        if len(pts) >= 4:  # drop one outlier pass
            keep = np.abs(resid) < 2.5 * (np.std(resid) + 1e-6)
            if keep.sum() >= 2:
                slope, icpt = np.polyfit(pts[keep, 0], pts[keep, 1], 1)
                resid = pts[:, 1] - (slope * pts[:, 0] + icpt)
        icpt += resid.min() if extreme == "top" else resid.max()
    else:
        slope = 0.0
        icpt = pts[:, 1].min() if extreme == "top" else pts[:, 1].max()
    return (0.0, icpt), (1.0, slope + icpt)


def _cols_to_slots(cols, w: int) -> List[List[Tuple[float, float]]]:
    """cols: [(top_point, bottom_point)] for each divider inside one band -> bay quads."""
    if len(cols) < 2:
        return []
    cols = sorted(cols, key=lambda c: (c[0][0] + c[1][0]) / 2.0)
    mids = [(c[0][0] + c[1][0]) / 2.0 for c in cols]
    gaps = np.diff(mids)
    med = float(np.median(gaps)) if len(gaps) else 0.0
    merged = [cols[0]]
    for c, g in zip(cols[1:], gaps):  # double painted lines / duplicates
        if g < max(0.3 * med, 0.015 * w):
            pa, pb = merged[-1]
            merged[-1] = (((pa[0] + c[0][0]) / 2, (pa[1] + c[0][1]) / 2), ((pb[0] + c[1][0]) / 2, (pb[1] + c[1][1]) / 2))
        else:
            merged.append(c)
    if len(merged) < 2:
        return []
    mids = [(c[0][0] + c[1][0]) / 2.0 for c in merged]
    gaps = np.diff(mids)
    med = float(np.median(gaps))
    slots = []
    for (L, R), g in zip(zip(merged[:-1], merged[1:]), gaps):
        if g < 0.45 * med:
            continue
        quad = [L[0], R[0], R[1], L[1]]
        n = max(1, min(6, int(round(g / med)) if med > 0 else 1))
        if n == 1:
            slots.append(quad)
        else:  # a divider is hidden (car on it / worn paint): split evenly with perspective
            f = _square_to_quad(quad)
            for i in range(n):
                u0, u1 = i / n, (i + 1) / n
                slots.append([f(u0, 0), f(u1, 0), f(u1, 1), f(u0, 1)])
    return slots


def _paint_support(mask_d: np.ndarray, a, b, samples: int = 24) -> float:
    h, w = mask_d.shape
    hits = 0
    for t in np.linspace(0.05, 0.95, samples):
        x = int(round(a[0] + (b[0] - a[0]) * t))
        y = int(round(a[1] + (b[1] - a[1]) * t))
        if 0 <= x < w and 0 <= y < h and mask_d[y, x]:
            hits += 1
    return hits / float(samples)


def _row_slots(divs: List[Dict[str, Any]], edges: List[Dict[str, Any]], w: int, h: int,
               mask_d: Optional[np.ndarray]) -> List[List[Tuple[float, float]]]:
    tops = [min(d["p1"], d["p2"], key=lambda p: p[1]) for d in divs]
    bots = [max(d["p1"], d["p2"], key=lambda p: p[1]) for d in divs]
    top_b = _boundary(tops, "top")
    bot_b = _boundary(bots, "bottom")
    xs = [p[0] for p in tops + bots]
    cx = (min(xs) + max(xs)) / 2.0
    row_w = max(xs) - min(xs)

    def y_at(line, x=None):
        p = _intersect(line[0], line[1], (cx if x is None else x, 0), (cx if x is None else x, h))
        return p[1] if p else None

    # Snap the fitted boundaries to a painted front/back line when one is close
    def snap(b):
        best, best_d = b, 0.08 * h
        yb = y_at(b)
        for e in edges:
            ye = y_at((e["p1"], e["p2"]))
            if e["len"] >= 0.15 * w and ye is not None and yb is not None and abs(ye - yb) < best_d:
                best, best_d = (e["p1"], e["p2"]), abs(ye - yb)
        return best
    top_b, bot_b = snap(top_b), snap(bot_b)
    yt, yb = y_at(top_b), y_at(bot_b)
    if yt is None or yb is None or yb <= yt:
        return []

    # Interior painted lines across the row (aisle edges / back-to-back rows) split it into bands
    interior = []
    for e in edges:
        ye = y_at((e["p1"], e["p2"]))
        if ye is not None and e["len"] >= 0.4 * row_w and yt + 0.08 * (yb - yt) < ye < yb - 0.08 * (yb - yt):
            if all(abs(ye - y_at(o)) > 0.05 * (yb - yt) for o in interior):
                interior.append((e["p1"], e["p2"]))
    bounds = [top_b] + sorted(interior, key=lambda l: y_at(l)) + [bot_b]

    slots = []
    for b0, b1 in zip(bounds[:-1], bounds[1:]):
        cols = []
        for d in divs:
            a = _intersect(d["p1"], d["p2"], b0[0], b0[1])
            c = _intersect(d["p1"], d["p2"], b1[0], b1[1])
            if not (a and c and c[1] > a[1]):
                continue
            # only dividers actually painted inside this band (skips the aisle between two rows)
            if mask_d is not None and len(bounds) > 2 and _paint_support(mask_d, a, c) < 0.35:
                continue
            cols.append((a, c))
        slots += _cols_to_slots(cols, w)
    return slots


def _group_rows(divs: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Dividers of the same row overlap vertically; separate rows (near/far side of an aisle) do not."""
    n = len(divs)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ranges = [(min(d["p1"][1], d["p2"][1]), max(d["p1"][1], d["p2"][1])) for d in divs]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = ranges[i], ranges[j]
            ov = min(a[1], b[1]) - max(a[0], b[0])
            if ov > 0.4 * min(a[1] - a[0], b[1] - b[0]):
                parent[find(i)] = find(j)
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(divs[i])
    return [g for g in groups.values() if len(g) >= 2]


def _detect_oriented(lines: List[Dict[str, Any]], w: int, h: int, mask_d: Optional[np.ndarray] = None,
                     horiz_tol: float = 22.0):
    def tilt(l):  # degrees away from horizontal
        a = l["ang"]
        return min(a, 180 - a)
    divs = [l for l in lines if tilt(l) >= horiz_tol and l["len"] >= 0.08 * h]
    edges = [l for l in lines if tilt(l) < horiz_tol]
    slots = []
    for row in sorted(_group_rows(divs), key=lambda r: np.mean([min(d["p1"][1], d["p2"][1]) for d in r])):
        slots += _row_slots(row, edges, w, h, mask_d)
    return slots


def detect_slots_from_lines(frame: np.ndarray, colors: Optional[List[str]] = None) -> Dict[str, Any]:
    colors = [c for c in (colors or ["yellow", "blue"]) if c in ("yellow", "blue", "white")] or ["yellow", "blue"]
    h, w = frame.shape[:2]
    scale = 1.0
    if max(h, w) > 1600:  # big phone photos: work on a smaller copy, coordinates are normalised anyway
        scale = 1600.0 / max(h, w)
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        h, w = frame.shape[:2]
    mask = paint_mask(frame, colors)
    diag = float(np.hypot(h, w))
    lines = _merge_segments(_segments(mask), diag)
    mask_d = cv2.dilate(mask, np.ones((7, 7), np.uint8))

    slots = _detect_oriented(lines, w, h, mask_d)
    orientation = "front"
    if len(slots) < 2:
        # camera looks along the row: swap axes, detect, swap back
        sw = [{**l, "p1": (l["p1"][1], l["p1"][0]), "p2": (l["p2"][1], l["p2"][0]),
               "ang": float(np.degrees(np.arctan2(l["p2"][0] - l["p1"][0], l["p2"][1] - l["p1"][1])) % 180.0)}
              for l in lines]
        alt = _detect_oriented(sw, h, w, cv2.transpose(mask_d))
        if len(alt) > len(slots):
            slots = [[(p[1], p[0]) for p in q] for q in alt]
            orientation = "side"

    norm = lambda p: [round(min(0.999, max(0.001, p[0] / w)), 3), round(min(0.999, max(0.001, p[1] / h)), 3)]
    out_slots = []
    for q in slots:
        pts = [norm(p) for p in q]
        area = abs(cv2.contourArea(np.array(pts, dtype=np.float32)))
        if area >= 0.002:  # ignore slivers
            out_slots.append(pts)
    return {
        "slots": out_slots,
        "lines": [[*norm(l["p1"]), *norm(l["p2"])] for l in lines],
        "paint_pixels": int(cv2.countNonZero(mask)),
        "orientation": orientation,
        "colors": colors,
    }
