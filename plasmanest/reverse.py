# SPDX-License-Identifier: GPL-2.0-or-later
"""The cut direction of a mirrored figure turned back round.

A mirror image runs the other way round: a part cut clockwise is cut anticlockwise, which puts
the good side of a plasma cut on the scrap, and QtPlasmaC takes a hole cut clockwise for a
mistake. reversed_program makes a program whose every closed contour runs the other way, so that
once mirrored it runs the way the original did.

A cut, from M3 to M5, is a lead-in, a closed contour, an overcut along the start of the contour
and a lead-out; all but the contour may be missing. The contour is cut the other way round from
the same point, and the overcut covers the same length along it. The lead-in is reflected where
it meets the contour, so that it keeps its shape and its angle to the contour but meets it from
the other side, as an arc lead-in must; the lead-out likewise where it leaves the contour.

A cut that cannot be taken apart like that, or whose reflected lead-in or lead-out would cross
a contour or pierce closer to it, raises ValueError: such a figure is not mirrored. No Qt here,
so it can be tested on its own.
"""

import math

from .gcode import Move, Op, Program, flatten_arc, format_number, split_comments, tokenize

CLOSE = 1e-3            # mm: points this close are the same point
NEARLY_CLOSED = 0.05    # mm: closer than this, but not closed, is refused rather than guessed at
MIN_PIERCE = 1.0        # mm a moved pierce point must stay from the contours, unless it was closer
JUNCTION = 0.01         # mm: a lead-in may touch its contour this close to where it joins it
COMPENSATION = (41.0, 42.0, 41.1, 42.1)


# ------------------------------------------------------------------------------ geometry

def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _unit(dx, dy):
    length = math.hypot(dx, dy)
    return None if length < 1e-9 else (dx / length, dy / length)


def _arc(move):
    """(centre, radius, start angle, sweep) of an arc move; the sweep is negative clockwise."""
    cx, cy = move.start[0] + move.center[0], move.start[1] + move.center[1]
    radius = math.hypot(move.start[0] - cx, move.start[1] - cy)
    a0 = math.atan2(move.start[1] - cy, move.start[0] - cx)
    a1 = math.atan2(move.end[1] - cy, move.end[0] - cx)
    sweep = a1 - a0
    if move.mode == 3:
        if sweep <= 1e-9:
            sweep += 2 * math.pi
    elif sweep >= -1e-9:
        sweep -= 2 * math.pi
    return (cx, cy), radius, a0, sweep


def _length(move):
    if move.center is None:
        return _distance(move.start, move.end)
    _, radius, _, sweep = _arc(move)
    return radius * abs(sweep)


def _tangents(move):
    """Unit directions at the start and the end of a move, or None for a move of no length."""
    if move.center is None:
        direction = _unit(move.end[0] - move.start[0], move.end[1] - move.start[1])
        return direction, direction
    (cx, cy), _, _, _ = _arc(move)
    sign = 1.0 if move.mode == 3 else -1.0
    start = _unit(-sign * (move.start[1] - cy), sign * (move.start[0] - cx))
    end = _unit(-sign * (move.end[1] - cy), sign * (move.end[0] - cx))
    return start, end


def _start_direction(moves):
    for move in moves:
        direction = _tangents(move)[0]
        if direction is not None:
            return direction
    return None


def _end_direction(moves):
    for move in reversed(moves):
        direction = _tangents(move)[1]
        if direction is not None:
            return direction
    return None


def _backwards(move):
    """The move run from its end to its start."""
    center = None
    if move.center is not None:
        center = (move.start[0] + move.center[0] - move.end[0], move.start[1] + move.center[1] - move.end[1])
    return Move({2: 3, 3: 2}.get(move.mode, move.mode), move.end, move.start, center, False, move.torch, [],
                move.comment, move.cut)


def _part_of(move, length):
    """The first length mm of a move."""
    if move.center is None:
        t = length / _distance(move.start, move.end)
        end = (move.start[0] + t * (move.end[0] - move.start[0]), move.start[1] + t * (move.end[1] - move.start[1]))
    else:
        (cx, cy), radius, a0, sweep = _arc(move)
        angle = a0 + math.copysign(length / radius, sweep)
        end = (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
    return Move(move.mode, move.start, end, move.center, False, move.torch, [], move.comment, move.cut)


class _Reflection:
    """The mirror that takes the point origin to target, and the direction there to new_direction:
    a shape keeps its angle to the contour but lies the other way along it."""

    def __init__(self, origin, target, direction, new_direction):
        self.origin, self.target = origin, target
        u = _unit(direction[0] + new_direction[0], direction[1] + new_direction[1])
        self.u = u if u is not None else (-direction[1], direction[0])

    def vector(self, v):
        dot = 2 * (v[0] * self.u[0] + v[1] * self.u[1])
        return (dot * self.u[0] - v[0], dot * self.u[1] - v[1])

    def point(self, p):
        x, y = self.vector((p[0] - self.origin[0], p[1] - self.origin[1]))
        return (self.target[0] + x, self.target[1] + y)

    def move(self, move):
        center = None if move.center is None else self.vector(move.center)
        return Move({2: 3, 3: 2}.get(move.mode, move.mode), self.point(move.start), self.point(move.end), center,
                    False, move.torch, [], move.comment, move.cut)


def _within_sweep(angle, a0, sweep, slack=1e-9):
    if sweep >= 0:
        return (angle - a0) % (2 * math.pi) <= sweep + slack
    return (a0 - angle) % (2 * math.pi) <= -sweep + slack


def _primitive(move):
    if move.center is None:
        return ("line", move.start, move.end)
    center, radius, a0, sweep = _arc(move)
    return ("arc", center, radius, a0, sweep, move.start, move.end)


def _box(primitive):
    if primitive[0] == "line":
        a, b = primitive[1], primitive[2]
        return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])
    (cx, cy), r = primitive[1], primitive[2]
    return cx - r, cy - r, cx + r, cy + r


def _on_line(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 < 1e-18:
        return _distance(point, a) <= CLOSE
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length2
    return -1e-9 <= t <= 1 + 1e-9 and abs((point[0] - a[0]) * dy - (point[1] - a[1]) * dx) / math.sqrt(length2) <= 1e-7


def _on_arc(point, arc):
    _, (cx, cy), r, a0, sweep, start, end = arc
    if abs(math.hypot(point[0] - cx, point[1] - cy) - r) > 1e-6:
        return False
    return _within_sweep(math.atan2(point[1] - cy, point[0] - cx), a0, sweep) or \
        _distance(point, start) <= 1e-9 or _distance(point, end) <= 1e-9


def _on(point, primitive):
    return _on_line(point, primitive[1], primitive[2]) if primitive[0] == "line" else _on_arc(point, primitive)


def _crossings(p, q):
    """Points where two primitives meet; for overlapping ones, the ends of the overlap and its middle."""
    if p[0] == "arc" and q[0] == "line":
        p, q = q, p
    if p[0] == "line" and q[0] == "line":
        a, b, c, d = p[1], p[2], q[1], q[2]
        r = (b[0] - a[0], b[1] - a[1])
        s = (d[0] - c[0], d[1] - c[1])
        denominator = r[0] * s[1] - r[1] * s[0]
        if abs(denominator) > 1e-12 * max(1.0, math.hypot(*r) * math.hypot(*s)):
            t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / denominator
            u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / denominator
            if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
                return [(a[0] + t * r[0], a[1] + t * r[1])]
            return []
        ends = [x for x in (a, b) if _on_line(x, c, d)] + [x for x in (c, d) if _on_line(x, a, b)]
        if len(ends) >= 2:
            first, last = max(((x, y) for x in ends for y in ends), key=lambda pair: _distance(*pair))
            ends.append(((first[0] + last[0]) / 2, (first[1] + last[1]) / 2))
        return ends
    if p[0] == "line":
        a, b = p[1], p[2]
        (cx, cy), r = q[1], q[2]
        dx, dy = b[0] - a[0], b[1] - a[1]
        fx, fy = a[0] - cx, a[1] - cy
        qa = dx * dx + dy * dy
        if qa < 1e-18:
            return [a] if _on_arc(a, q) else []
        qb = 2 * (fx * dx + fy * dy)
        qc = fx * fx + fy * fy - r * r
        disc = qb * qb - 4 * qa * qc
        if disc < -1e-9 * max(1.0, qb * qb):
            return []
        root = math.sqrt(max(disc, 0.0))
        points = []
        for t in ((-qb - root) / (2 * qa), (-qb + root) / (2 * qa)):
            if -1e-9 <= t <= 1 + 1e-9:
                point = (a[0] + t * dx, a[1] + t * dy)
                angle = math.atan2(point[1] - cy, point[0] - cx)
                if _within_sweep(angle, q[3], q[4], 1e-7):
                    points.append(point)
        return points
    (c1, r1), (c2, r2) = (p[1], p[2]), (q[1], q[2])
    d = _distance(c1, c2)
    if d < 1e-9 and abs(r1 - r2) < 1e-9:
        ends = [x for x in (p[5], p[6]) if _on_arc(x, q)] + [x for x in (q[5], q[6]) if _on_arc(x, p)]
        # Arcs on one circle overlap somewhere between where they meet.
        for x in list(ends):
            for y in list(ends):
                mid = math.atan2((x[1] + y[1]) / 2 - c1[1], (x[0] + y[0]) / 2 - c1[0])
                for angle in (mid, mid + math.pi):
                    point = (c1[0] + r1 * math.cos(angle), c1[1] + r1 * math.sin(angle))
                    if _on_arc(point, p) and _on_arc(point, q):
                        ends.append(point)
        return ends
    if d < 1e-9 or d > r1 + r2 + 1e-9 or d < abs(r1 - r2) - 1e-9:
        return []
    along = (d * d + r1 * r1 - r2 * r2) / (2 * d)
    height = math.sqrt(max(r1 * r1 - along * along, 0.0))
    ux, uy = (c2[0] - c1[0]) / d, (c2[1] - c1[1]) / d
    base = (c1[0] + along * ux, c1[1] + along * uy)
    candidates = [(base[0] - height * uy, base[1] + height * ux), (base[0] + height * uy, base[1] - height * ux)]
    return [x for x in candidates if _on_arc(x, p) and _on_arc(x, q)]


def _distance_to(point, primitive):
    if primitive[0] == "line":
        a, b = primitive[1], primitive[2]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length2))
        return math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)
    _, (cx, cy), r, a0, sweep, start, end = primitive
    if _within_sweep(math.atan2(point[1] - cy, point[0] - cx), a0, sweep):
        return abs(math.hypot(point[0] - cx, point[1] - cy) - r)
    return min(_distance(point, start), _distance(point, end))


def _points(moves):
    points = [moves[0].start]
    for move in moves:
        points += flatten_arc(move.start, move.center, move.end, move.mode) if move.center is not None else [move.end]
    return points


def _area(points):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))) / 2


# ----------------------------------------------------------------------------- the cuts

def _find_loop(moves, where):
    """(first, last) index of the moves forming the first closed contour, or None."""
    for first in range(len(moves)):
        for last in range(first, len(moves)):
            gap = _distance(moves[last].end, moves[first].start)
            if gap > NEARLY_CLOSED:
                continue
            points = _points(moves[first:last + 1])
            if gap <= CLOSE:
                if _area(points) > 1e-6:
                    return first, last
            elif sum(_distance(a, b) for a, b in zip(points, points[1:])) > 20 * NEARLY_CLOSED:
                # Not just two short moves of a fine polyline: a contour left open by rounding.
                raise ValueError(f"the cut {where} comes within {gap:.3f} mm of closing a contour without "
                                 "closing it")
    return None


def _overcut(rest, contour):
    """How many moves at the start of rest run over the start of the contour again."""
    count = 0
    for move, along in zip(rest, contour):
        if move.mode != along.mode or _distance(move.start, along.start) > CLOSE:
            break
        if move.center is None:
            if not _on_line(move.end, along.start, along.end):
                break
        else:
            centre, radius, _, sweep = _arc(along)
            own, own_radius, _, own_sweep = _arc(move)
            if _distance(centre, own) > CLOSE or abs(radius - own_radius) > CLOSE \
                    or abs(own_sweep) > abs(sweep) + 1e-6:
                break
        count += 1
        if _distance(move.end, along.end) > CLOSE:
            break  # partly along this move: the overcut ends here
    return count


def _take(moves, length):
    """The moves covering the first length mm of moves."""
    result = []
    for move in moves:
        if length <= CLOSE / 10:
            break
        here = _length(move)
        if here <= length + CLOSE / 10:
            result.append(move)
            length -= here
        else:
            result.append(_part_of(move, length))
            length = 0.0
    return result


class _Cut:
    """One cut, from its first move with the torch on to its last."""

    def __init__(self, number, start, last, moves, inserts):
        self.number, self.start, self.last = number, start, last
        self.moves, self.inserts = moves, inserts  # inserts: (number of moves before it, op)
        self.where = f"{number} (from X{format_number(moves[0].start[0])} Y{format_number(moves[0].start[1])})"
        self.loop = _find_loop(moves, self.where)
        if self.loop is None:
            return
        first, last_move = self.loop
        self.lead_in = moves[:first]
        self.contour = moves[first:last_move + 1]
        rest = moves[last_move + 1:]
        over = _overcut(rest, self.contour)
        self.over, self.lead_out = rest[:over], rest[over:]
        if self.lead_out and _find_loop(self.lead_out, self.where) is not None:
            raise ValueError(f"the cut {self.where} cuts more than one contour")
        self.primitives = [_primitive(m) for m in self.contour if _length(m) > 1e-9]

    def reversed(self, contours):
        """The cut's moves, raw lines included, turned round; checked against all contours."""
        turned = [_backwards(move) for move in reversed(self.contour)]
        over = _take(turned, sum(_length(m) for m in self.over))
        junction = self.contour[0].start
        lead_in = []
        if self.lead_in:
            flip = _Reflection(junction, junction, _start_direction(self.contour), _start_direction(turned))
            lead_in = [flip.move(m) for m in self.lead_in]
        lead_out = []
        if self.lead_out:
            old_end, new_end = (self.over or self.contour)[-1].end, (over or turned)[-1].end
            flip = _Reflection(old_end, new_end, _end_direction(self.over or self.contour),
                               _end_direction(over or turned))
            lead_out = [flip.move(m) for m in self.lead_out]
        self._check(lead_in, junction, contours, "lead-in")
        self._check(lead_out, (over or turned)[-1].end, contours, "lead-out")
        if lead_in:
            old = min(_distance_to(self.lead_in[0].start, p) for p in self.primitives)
            new = min(_distance_to(lead_in[0].start, p) for p in self.primitives)
            if new < min(old, MIN_PIERCE) - 1e-6:
                raise ValueError(f"the cut {self.where}, mirrored, would pierce {new:.2f} mm from its contour "
                                 f"instead of {old:.2f} mm")

        # The raw lines between the parts stay between them; elsewhere they are refused.
        parts = [lead_in, turned, over, lead_out]
        bounds = [len(self.lead_in), len(self.lead_in) + len(self.contour),
                  len(self.lead_in) + len(self.contour) + len(self.over)]
        after = {0: [], 1: [], 2: []}
        comments = []
        for position, op in self.inserts:
            if not split_comments(op.text)[0]:
                comments.append(op)
            elif position in bounds:
                after[bounds.index(position)].append(op)
            else:
                raise ValueError(f"the cut {self.where} has \"{op.text}\" in the middle of a lead-in, contour "
                                 "or lead-out")
        result = list(comments)
        for index, part in enumerate(parts):
            result += [Op("move", move=m) for m in part]
            if index < 3:
                result += after[index]
        return result, (lead_in or turned)[0].start

    def _check(self, moves, junction, contours, name):
        for move in moves:
            if _length(move) <= 1e-9:
                continue
            primitive = _primitive(move)
            x0, y0, x1, y1 = _box(primitive)
            for owner, other, (ox0, oy0, ox1, oy1) in contours:
                if ox0 > x1 + CLOSE or ox1 < x0 - CLOSE or oy0 > y1 + CLOSE or oy1 < y0 - CLOSE:
                    continue
                for point in _crossings(primitive, other):
                    if owner is self and _distance(point, junction) <= JUNCTION:
                        continue
                    raise ValueError(f"the cut {self.where}, mirrored, would have its {name} cross a contour "
                                     f"at X{format_number(round(point[0], 3))} Y{format_number(round(point[1], 3))}")


# ----------------------------------------------------------------------------- the file

def _words(text):
    code, _ = split_comments(text)
    if not code or code.startswith("#"):
        return []
    try:
        return tokenize(code)
    except ValueError:
        return []


def _float(value):
    try:
        return round(float(value), 2)
    except ValueError:
        return None


def _has_code(op, letter, numbers):
    return any(word_letter == letter and _float(value) in numbers for word_letter, value in _words(op.text))


def reversed_program(program):
    """The program with every closed contour cut the other way round, or ValueError saying why
    it cannot be done safely. The result has the same centre, so a placement maps it to the same
    place. It is worked out once per program."""
    cached = program.__dict__.get("_reversed")
    if isinstance(cached, ValueError):
        raise cached
    if cached is not None:
        return cached
    try:
        result = _reverse(program)
    except ValueError as error:
        program._reversed = error
        raise
    program._reversed = result
    return result


def can_reverse(program):
    try:
        reversed_program(program)
    except ValueError:
        return False
    return True


def _cuts(ops):
    index = 0
    number = 0
    while index < len(ops):
        op = ops[index]
        if op.kind != "move" or not op.move.torch:
            index += 1
            continue
        number += 1
        start = last = scan = index
        while scan < len(ops):
            here = ops[scan]
            if here.kind == "move":
                if not here.move.torch:
                    break
                last = scan
            elif _has_code(here, "M", (3.0, 5.0)):
                break
            scan += 1
        moves, inserts = [], []
        for i in range(start, last + 1):
            if ops[i].kind == "move":
                moves.append(ops[i].move)
            else:
                inserts.append((len(moves), ops[i]))
        where = f"{number} (from X{format_number(moves[0].start[0])} Y{format_number(moves[0].start[1])})"
        if any(move.mode == 0 for move in moves):
            raise ValueError(f"the cut {where} has a rapid move (G0) with the torch on")
        # Words such as F may only come first; they are kept first.
        if any(move.extras for move in moves[1:]) or any(not w.upper().startswith("F") for w in moves[0].extras):
            raise ValueError(f"the cut {where} has words other than X, Y, I and J after its first move")
        yield _Cut(number, start, last, moves, inserts)
        index = last + 1


def _reverse(program):
    ops = program.ops
    moves = [op.move for op in ops if op.kind == "move"]
    if not any(move.torch for move in moves):
        raise ValueError("the file has no M3, so it is unknown which moves are cuts")
    if any(move.incremental for move in moves):
        raise ValueError("the file has incremental moves (G91)")
    for op in ops:
        for text in (op.move.extras if op.kind == "move" else [op.text]):
            if any(letter == "G" and _float(value) in COMPENSATION for letter, value in _words(text)):
                raise ValueError("the file uses cutter compensation (G41/G42)")

    cuts = list(_cuts(ops))
    contours = [(cut, p, _box(p)) for cut in cuts if cut.loop is not None for p in cut.primitives]
    replaced = {}
    for cut in cuts:
        if cut.loop is None:
            continue  # an open cut, such as a slit, has no inside to keep on the right side
        new_ops, pierce = cut.reversed(contours)
        for move in (op.move for op in new_ops if op.kind == "move"):
            move.extras = []
        next(op for op in new_ops if op.kind == "move").move.extras = list(cut.moves[0].extras)
        replaced[cut.start] = new_ops
        for i in range(cut.start + 1, cut.last + 1):
            replaced[i] = []
        if _distance(pierce, cut.moves[0].start) > 1e-9:
            # The move to the pierce point now goes to where the lead-in starts.
            before = next((i for i in range(cut.start - 1, -1, -1) if ops[i].kind == "move"), None)
            if before is None or ops[before].move.torch or ops[before].move.mode not in (0, 1) \
                    or before in replaced:
                raise ValueError(f"the cut {cut.where} has no move of its own to its pierce point")
            m = ops[before].move
            replaced[before] = [Op("move", move=Move(m.mode, m.start, pierce, None, False, m.torch,
                                                     list(m.extras), m.comment, m.cut))]

    result = list(ops)
    for i in sorted(replaced, reverse=True):
        result[i:i + 1] = replaced[i]
    lines = ["G21"]
    for op in result:
        if op.kind != "move":
            lines.append(op.text)
            continue
        m = op.move
        words = [f"G{m.mode}", f"X{format_number(m.end[0])}", f"Y{format_number(m.end[1])}"]
        if m.center is not None:
            words.append(f"I{format_number(m.center[0])} J{format_number(m.center[1])}")
        lines.append(" ".join(words + m.extras + ([m.comment] if m.comment else [])))
    reversed_ = Program(program.name, "\n".join(lines) + "\n")
    # The placement maps both about the original's centre.
    reversed_.center = program.center
    return reversed_
