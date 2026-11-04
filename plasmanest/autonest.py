# SPDX-License-Identifier: GPL-2.0-or-later
"""Automatic nesting: pack figures against the bottom edge of the sheet.

Each figure is represented by the convex hull of everything it cuts, lead-ins included, grown so
that two figures keep at least the chosen gap and every figure keeps the margin from the sheet's
edge. The hulls are dropped one at a time, largest first, to the lowest free position: the lowest
y, then the leftmost x. A figure lying wholly inside another's hull, like a hole drawn in a part,
moves with that figure as a group. Hulls make the result safe but not tight: concave parts, such
as an L, are not interlocked, and no figure is put inside another's hole.

No Qt here, so it can be tested on its own.
"""

import math
from bisect import bisect_right
from dataclasses import dataclass

from .nest import Placement, rotation

SIMPLIFY_TOLERANCE = 0.2  # mm a hull may shrink when simplified; the footprint grows by as much
ARC_STEP = math.pi / 8    # largest angle of one corner segment when a hull is grown
X_GRID = 32               # extra x positions tried across the sheet for every figure
EPS = 1e-7
TIE = 0.01              # mm: closer results count as equal


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def convex_hull(points):
    """Anticlockwise convex hull, without collinear points."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _line_distance(p, a, b):
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs(_cross(a, b, p)) / length


def simplify_hull(hull, tolerance=SIMPLIFY_TOLERANCE):
    """Drop hull corners that lie within tolerance of the line past them. The result is convex,
    inside the hull, and no point of the hull is more than tolerance outside it."""
    if len(hull) <= 4:
        return list(hull)
    # Start at the leftmost corner, which a convex polygon always keeps as a real corner.
    start = min(range(len(hull)), key=lambda i: hull[i])
    ring = hull[start:] + hull[:start]
    kept = [ring[0]]
    i = 0
    n = len(ring)
    while i < n - 1:
        j = i + 1
        while j + 1 <= n and all(_line_distance(ring[k], ring[i], ring[(j + 1) % n]) <= tolerance
                                 for k in range(i + 1, j + 1)):
            j += 1
        if j >= n:
            break
        kept.append(ring[j])
        i = j
    return kept if len(kept) >= 3 else list(hull)


def grow_hull(hull, distance):
    """A convex polygon holding every point within distance of the convex hull."""
    if len(hull) == 1:
        (x, y), = hull
        radius = distance / math.cos(math.pi / 8)
        return [(x + radius * math.cos(k * math.pi / 4 + math.pi / 8),
                 y + radius * math.sin(k * math.pi / 4 + math.pi / 8)) for k in range(8)]
    points = []
    n = len(hull)
    for i in range(n):
        prev, here, nxt = hull[i - 1], hull[i], hull[(i + 1) % n]
        # Outward normals of the edges before and after the corner, anticlockwise hull.
        a1 = math.atan2(-(here[0] - prev[0]), here[1] - prev[1])
        a2 = math.atan2(-(nxt[0] - here[0]), nxt[1] - here[1])
        turn = (a2 - a1) % (2 * math.pi)
        if n == 2:
            turn = math.pi
        steps = max(1, math.ceil(turn / ARC_STEP - 1e-9))
        step = turn / steps
        radius = distance / math.cos(step / 2)
        # Where neighbouring tangents of the corner's arc meet: the arc lies wholly inside.
        for k in range(steps):
            angle = a1 + step * (k + 0.5)
            points.append((here[0] + radius * math.cos(angle), here[1] + radius * math.sin(angle)))
    return convex_hull(points)


class Footprint:
    """A convex anticlockwise polygon with its lower and upper edge as functions of x."""

    def __init__(self, polygon):
        self.polygon = polygon
        xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
        self.minx, self.maxx, self.miny, self.maxy = min(xs), max(xs), min(ys), max(ys)
        if len(polygon) >= 3:
            self.lower, self.upper = self._split(polygon)
        else:
            self.lower = self._chain(polygon, min)
            self.upper = self._chain(polygon, max)
        self._key = None

    @property
    def area(self):
        polygon = self.polygon
        return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(polygon, polygon[1:] + polygon[:1]))) / 2

    @property
    def key(self):
        """The shape without its position: equal for copies of a figure at the same angle."""
        if self._key is None:
            self._key = tuple((round(x - self.minx, 6), round(y - self.miny, 6)) for x, y in self.polygon)
        return self._key

    @staticmethod
    def _split(polygon):
        """The lower and upper chain of a convex anticlockwise polygon, both from left to right."""
        n = len(polygon)
        order = range(n)
        left_low = min(order, key=lambda i: (polygon[i][0], polygon[i][1]))
        right_low = min(order, key=lambda i: (-polygon[i][0], polygon[i][1]))
        right_high = min(order, key=lambda i: (-polygon[i][0], -polygon[i][1]))
        left_high = min(order, key=lambda i: (polygon[i][0], -polygon[i][1]))

        def walk(start, end):
            points = [polygon[start]]
            i = start
            while i != end:
                i = (i + 1) % n
                points.append(polygon[i])
            return points

        lower = walk(left_low, right_low)
        upper = walk(right_high, left_high)[::-1]
        return ([p[0] for p in lower], [p[1] for p in lower]), ([p[0] for p in upper], [p[1] for p in upper])

    @staticmethod
    def _chain(polygon, pick):
        best = {}
        for x, y in polygon:
            best[x] = pick(best[x], y) if x in best else y
        xs = sorted(best)
        ys = [best[x] for x in xs]
        # Keep only the convex chain: the corners that are on the lower (or upper) edge.
        chain = []
        sign = 1 if pick is min else -1
        for p in zip(xs, ys):
            while len(chain) >= 2 and sign * _cross(chain[-2], chain[-1], p) <= 0:
                chain.pop()
            chain.append(p)
        return [p[0] for p in chain], [p[1] for p in chain]

    @staticmethod
    def at(chain, x):
        xs, ys = chain
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        i = bisect_right(xs, x)
        x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    def moved(self, dx, dy):
        return Footprint([(x + dx, y + dy) for x, y in self.polygon])

    def contains_point(self, point):
        n = len(self.polygon)
        if n < 3:
            return False
        return all(_cross(self.polygon[i], self.polygon[(i + 1) % n], point) >= -1e-6 for i in range(n))


def minkowski_sum(p, q):
    """Minkowski sum of two convex anticlockwise polygons, in linear time."""
    def from_bottom(poly):
        start = min(range(len(poly)), key=lambda i: (poly[i][1], poly[i][0]))
        ring = poly[start:] + poly[:start]
        return ring + ring[:2]
    p, q = from_bottom(p), from_bottom(q)
    result = []
    i = j = 0
    while i < len(p) - 2 or j < len(q) - 2:
        result.append((p[i][0] + q[j][0], p[i][1] + q[j][1]))
        turn = ((p[i + 1][0] - p[i][0]) * (q[j + 1][1] - q[j][1])
                - (p[i + 1][1] - p[i][1]) * (q[j + 1][0] - q[j][0]))
        if turn >= 0 and i < len(p) - 2:
            i += 1
        if turn <= 0 and j < len(q) - 2:
            j += 1
    return result


class Obstruction:
    """Every shift (x, y) at which a moving footprint overlaps a fixed one: the Minkowski sum of
    the fixed footprint and the mirrored moving one. Worked out once, it gives the blocked range
    of y for any x with two look-ups. It is worked out for the shapes alone, so copies of a figure
    share it."""

    def __init__(self, moving, fixed):
        self.region = Footprint(minkowski_sum([(x - fixed.minx, y - fixed.miny) for x, y in fixed.polygon],
                                              [(moving.minx - x, moving.miny - y) for x, y in moving.polygon]))

    def interval(self, moving, x, fixed):
        """The open range of y blocked for the moving footprint shifted by x, or None."""
        region = self.region
        shift = x + moving.minx - fixed.minx
        if not region.minx + EPS < shift < region.maxx - EPS:
            return None
        offset = fixed.miny - moving.miny
        return Footprint.at(region.lower, shift) + offset, Footprint.at(region.upper, shift) + offset


def figure_points(placement):
    """Every cut point of a figure, on the sheet."""
    return [placement.map_point(p) for path in placement.program.paths
            if path.kind == "cut" for p in path.points]


def group_figures(placements):
    """Lists of indexes into placements that must move together: a figure lying wholly inside
    another figure's hull, such as a hole drawn in a part, stays with it."""
    hulls = [convex_hull(figure_points(p)) for p in placements]
    footprints = [Footprint(h) if len(h) >= 3 else None for h in hulls]
    parent = list(range(len(placements)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, inner in enumerate(hulls):
        for j, outer in enumerate(footprints):
            if i == j or outer is None:
                continue
            xs, ys = [p[0] for p in inner], [p[1] for p in inner]
            if min(xs) < outer.minx - 1e-6 or max(xs) > outer.maxx + 1e-6 \
                    or min(ys) < outer.miny - 1e-6 or max(ys) > outer.maxy + 1e-6:
                continue
            if all(outer.contains_point(p) for p in inner):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(placements)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


@dataclass
class Move:
    """Where autonest puts a figure."""
    placement: Placement
    x: float
    y: float
    angle: float


class _Group:
    def __init__(self, placements):
        self.placements = placements
        points = [p for placement in placements for p in figure_points(placement)]
        self.hull = convex_hull(points)
        xs, ys = [p[0] for p in self.hull], [p[1] for p in self.hull]
        self.pivot = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        self.area = Footprint(self.hull).area if len(self.hull) >= 3 else 0.0

    def turned(self, degrees):
        """The hull turned by degrees about the pivot, with the pivot at the origin."""
        c, s = rotation(degrees)
        return [(c * (x - self.pivot[0]) - s * (y - self.pivot[1]),
                 s * (x - self.pivot[0]) + c * (y - self.pivot[1])) for x, y in self.hull]

    def moves(self, degrees, dx, dy):
        c, s = rotation(degrees)
        result = []
        for placement in self.placements:
            px, py = placement.x - self.pivot[0], placement.y - self.pivot[1]
            result.append(Move(placement, c * px - s * py + dx, s * px + c * py + dy,
                               math.fmod(placement.angle + degrees, 360.0) % 360.0))
        return result


class _Sheet:
    """The sheet as seen by the drop: its edges, for contacts from below on a polygon sheet."""

    def __init__(self, sheet):
        self.sheet = sheet
        self.x0, self.y0, self.x1, self.y1 = sheet.bounds

    def contacts(self, footprint, x):
        """Every y at which the footprint, shifted by x, touches an edge of a polygon sheet."""
        result = []
        for (ax, ay), (bx, by) in self.sheet.edges:
            if abs(bx - ax) > EPS:
                for px, py in footprint.polygon:
                    X = px + x
                    if min(ax, bx) - EPS <= X <= max(ax, bx) + EPS:
                        result.append(ay + (by - ay) * (X - ax) / (bx - ax) - py)
            if footprint.minx + x - EPS <= ax <= footprint.maxx + x + EPS:
                result.append(ay - Footprint.at(footprint.lower, ax - x))
                result.append(ay - Footprint.at(footprint.upper, ax - x))
        return result

    def holds(self, footprint, x, y):
        if self.sheet.is_rectangle:
            return True
        points = [(px + x, py + y) for px, py in footprint.polygon]
        return all(self.sheet.contains_segment(a, b) for a, b in zip(points, points[1:] + points[:1]))


def _lowest(body, edge, sheet, obstacles, best_top, exact, floors):
    """The lowest, then leftmost, (x, y) for a turned figure, or None. body is grown by half the
    gap and meets the obstacles, edge is grown by the margin and must stay on the sheet."""
    # exact holds the Obstructions by the keys of both shapes, worked out the first time an
    # obstacle's box is in the way. floors holds, by shape and x, how high a figure of that shape
    # had to climb last time. Obstacles are only ever added, so the next copy can start there.
    xa, xb = sheet.x0 - edge.minx, sheet.x1 - edge.maxx
    ya, yb = sheet.y0 - edge.miny, sheet.y1 - edge.maxy
    if xb < xa - EPS or yb < ya - EPS:
        return None
    xb, yb = max(xa, xb), max(ya, yb)
    xs = {xa, xb}
    xs.update(xa + k * (xb - xa) / X_GRID for k in range(1, X_GRID))
    for o in obstacles:
        xs.add(o.maxx - body.minx)
        xs.add(o.minx - body.maxx)
    if not sheet.sheet.is_rectangle:
        for cx, _ in sheet.sheet.corners:
            xs.add(cx - edge.minx)
            xs.add(cx - edge.maxx)
    best = None
    obstacles = sorted(obstacles, key=lambda o: o.miny)
    # Rows of copies give the same positions many times over.
    for x in sorted({round(min(max(v, xa), xb), 4) for v in xs if xa - EPS <= v <= xb + EPS}):
        # Only obstacles in the figure's column, lowest first.
        column = [o for o in obstacles if o.maxx - EPS > body.minx + x and o.minx + EPS < body.maxx + x]
        contacts = sorted(sheet.contacts(edge, x)) if not sheet.sheet.is_rectangle else []
        floor = (body.key, x)
        y = max(ya, floors.get(floor, ya))
        while y <= yb + EPS:
            top = y + edge.maxy
            if (best is not None and top > best[0] - EPS) or (best_top is not None and top > best_top + EPS):
                break
            # The figure only rises, so obstacles it has passed are dropped for good.
            column = [o for o in column if o.maxy - body.miny > y + EPS]
            blocker = None
            for o in column:
                if o.miny - body.maxy >= y - EPS:
                    break  # this and every later obstacle lies above the figure
                key = (body.key, o.key)
                if key not in exact:
                    exact[key] = Obstruction(body, o)
                interval = exact[key].interval(body, x, o)
                if interval is not None and interval[0] + EPS < y < interval[1] - EPS:
                    blocker = interval
                    break
            if blocker is not None:
                y = blocker[1]
            elif sheet.holds(edge, x, y):
                best = (top, x, y)
                break
            else:
                # Off a polygon sheet: that only changes where the figure touches one of its edges.
                y = next((c for c in contacts if c > y + EPS), math.inf)
        floors[floor] = y
    return None if best is None else (best[1], best[2])


def autonest(sheet, moving, fixed=(), gap=10.0, margin=10.0, turn=True):
    """Pack the moving placements against the bottom edge of the sheet, around the fixed ones.

    Returns (moves, unplaced): a Move for every moving placement, and the placements that did not
    fit. Those are moved beside the sheet, to the right, so they cannot lie on the packed ones.
    Nothing is changed: the caller applies the moves."""
    if gap < 0 or margin < 0:
        raise ValueError("The gap and the margin cannot be negative.")
    moving = list(moving)
    groups = [_Group([moving[i] for i in indexes]) for indexes in group_figures(moving)]
    groups.sort(key=lambda g: -g.area)
    body_grow = gap / 2 + SIMPLIFY_TOLERANCE
    edge_grow = margin + SIMPLIFY_TOLERANCE
    obstacles = []
    for placement in fixed:
        hull = convex_hull(figure_points(placement))
        obstacles.append(Footprint(grow_hull(hull, gap / 2)))
    target = _Sheet(sheet)
    angles = (0.0, 90.0, 180.0, 270.0) if turn else (0.0,)
    moves, unplaced = [], []
    exact, floors = {}, {}
    for group in groups:
        best = None
        for degrees in angles:
            hull = simplify_hull(group.turned(degrees))
            body = Footprint(grow_hull(hull, body_grow))
            edge = Footprint(grow_hull(hull, edge_grow))
            found = _lowest(body, edge, target, obstacles, None if best is None else best[0], exact, floors)
            if found is None:
                continue
            top = found[1] + edge.maxy
            # A turn has to gain something; on a tie the figure keeps its angle.
            if best is None or top < best[0] - TIE or (abs(top - best[0]) <= TIE and found[0] < best[2] - TIE):
                best = (top, degrees, found[0], found[1], body)
        if best is None:
            unplaced.append(group)
            continue
        _, degrees, x, y, body = best
        obstacles.append(body.moved(x, y))
        moves += group.moves(degrees, x, y)
    # Beside the sheet, in a column from its bottom edge upwards, in their own orientation.
    right = max([target.x1] + [o.maxx for o in obstacles]) + 5 * max(gap, 10.0)
    y = target.y0
    for group in unplaced:
        footprint = Footprint(grow_hull(group.turned(0.0), SIMPLIFY_TOLERANCE)) \
            if len(group.hull) else None
        x = right - footprint.minx
        moves += group.moves(0.0, x, y - footprint.miny)
        y += footprint.maxy - footprint.miny + max(gap, 10.0)
    return moves, [p for group in unplaced for p in group.placements]
