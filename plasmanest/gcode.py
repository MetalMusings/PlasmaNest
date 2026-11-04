# SPDX-License-Identifier: GPL-2.0-or-later
"""Parser for G-code written for LinuxCNC/QtPlasmaC.

The parser turns a file into a list of operations in millimetres. Every XY move
is resolved to explicit numbers so that it can be rotated and moved later. Files
that cannot be transformed safely are refused with GCodeError: a wrong guess here
means a wrong cut in real steel.
"""

import math
import re
from dataclasses import dataclass, field

MM_PER_INCH = 25.4
ARC_CHORD_TOLERANCE = 0.05  # mm, preview only

# Parameters QtPlasmaC reads as lengths; they must follow the unit conversion.
LENGTH_PARAMS = {"#<oclength>", "#<h_diameter>", "#<a_diameter>", "#<m_diameter>",
                 "#<i_diameter>", "#<ocradius>"}

NUMBER_RE = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)")
ASSIGN_RE = re.compile(r"^(#<[^>]+>|#\d+)\s*=\s*(.+)$")
PARAM_RE = re.compile(r"#<[^>]+>|#\d+")

FUNCTIONS = {
    "sin": lambda v: math.sin(math.radians(v)),
    "cos": lambda v: math.cos(math.radians(v)),
    "tan": lambda v: math.tan(math.radians(v)),
    "asin": lambda v: math.degrees(math.asin(v)),
    "acos": lambda v: math.degrees(math.acos(v)),
    "atan": lambda v: math.degrees(math.atan(v)),
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "fix": math.floor,
    "fup": math.ceil,
    "exp": math.exp,
    "ln": math.log,
}


class GCodeError(Exception):
    """The file cannot be nested. line is the 1-based source line, if known."""

    def __init__(self, message, line=None):
        super().__init__(f"Line {line}: {message}" if line else message)
        self.line = line


class Unresolved(Exception):
    """An expression depends on a value only known inside LinuxCNC."""


@dataclass
class Move:
    mode: int                 # 0, 1, 2 or 3
    start: tuple
    end: tuple
    center: tuple = None      # arcs: offset from start to centre (I, J)
    incremental: bool = False
    torch: bool = False
    extras: list = field(default_factory=list)  # other words on the same line
    comment: str = ""
    cut: bool = False


@dataclass
class Op:
    kind: str                 # raw, material, material_wait, temp_material, lib_material, move
    text: str = ""
    move: Move = None


@dataclass
class PathPart:
    kind: str                 # cut, rapid or feed
    points: list


def normalize_param(name):
    return re.sub(r"\s+", "", name).lower()


class _Expr:
    """Recursive descent evaluator for LinuxCNC [...] expressions."""

    def __init__(self, text, params):
        self.s = text
        self.i = 0
        self.params = params

    def _ws(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def _peek(self):
        self._ws()
        return self.s[self.i] if self.i < len(self.s) else ""

    def parse(self):
        value = self._sum()
        if self._peek():
            raise ValueError(f"unexpected character in expression: {self.s}")
        return value

    def _sum(self):
        value = self._product()
        while self._peek() in ("+", "-"):
            op = self.s[self.i]
            self.i += 1
            rhs = self._product()
            value = value + rhs if op == "+" else value - rhs
        return value

    def _product(self):
        value = self._power()
        while True:
            c = self._peek()
            if c == "*" and self.s[self.i:self.i + 2] != "**":
                self.i += 1
                value *= self._power()
            elif c == "/":
                self.i += 1
                value /= self._power()
            elif self.s[self.i:self.i + 3].lower() == "mod":
                self.i += 3
                value = math.fmod(value, self._power())
            else:
                return value

    def _power(self):
        value = self._unary()
        self._ws()
        if self.s[self.i:self.i + 2] == "**":
            self.i += 2
            return value ** self._power()
        return value

    def _unary(self):
        c = self._peek()
        if c in ("+", "-"):
            self.i += 1
            value = self._unary()
            return -value if c == "-" else value
        return self._atom()

    def _atom(self):
        c = self._peek()
        if c == "[":
            self.i += 1
            value = self._sum()
            if self._peek() != "]":
                raise ValueError(f"missing ] in expression: {self.s}")
            self.i += 1
            return value
        if c == "#":
            match = PARAM_RE.match(self.s, self.i)
            if not match:
                raise ValueError(f"invalid parameter in expression: {self.s}")
            self.i = match.end()
            key = normalize_param(match.group())
            if key not in self.params:
                raise Unresolved(match.group())
            return self.params[key]
        match = NUMBER_RE.match(self.s, self.i)
        if match:
            self.i = match.end()
            return float(match.group())
        match = re.compile(r"[a-zA-Z]+").match(self.s, self.i)
        if match and match.group().lower() in FUNCTIONS:
            self.i = match.end()
            if self._peek() != "[":
                raise ValueError(f"function without argument in expression: {self.s}")
            return float(FUNCTIONS[match.group().lower()](self._atom()))
        raise ValueError(f"cannot read expression: {self.s}")


def evaluate(value, params):
    """Evaluate a word value (number, #param or [expr]) to a float."""
    return _Expr(value, params).parse()


def split_comments(line):
    """Return (code, comments). Parenthesised comments and ;-comments are removed."""
    code, comments, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == ";":
            comments.append(line[i:].rstrip())
            break
        if c == "(":
            end = line.find(")", i)
            end = len(line) - 1 if end < 0 else end
            comments.append(line[i:end + 1])
            i = end + 1
            continue
        code.append(c)
        i += 1
    return "".join(code).strip(), comments


def tokenize(code):
    """Split a block into (letter, value-text) words; spaces between are optional."""
    words, i, n = [], 0, len(code)
    while i < n:
        if code[i].isspace():
            i += 1
            continue
        letter = code[i].upper()
        if not (letter.isalpha() or letter == "$"):
            raise ValueError(f"unexpected character '{code[i]}'")
        i += 1
        while i < n and code[i].isspace():
            i += 1
        if i >= n:
            raise ValueError(f"the word {letter} has no value")
        if code[i] == "[":
            depth, start = 0, i
            while i < n:
                if code[i] == "#" and code[i + 1:i + 2] == "<":
                    # Names such as #<_hal[x]> hold brackets of their own.
                    i = code.find(">", i)
                    if i < 0:
                        raise ValueError("parameter name without >")
                elif code[i] == "[":
                    depth += 1
                elif code[i] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            if depth:
                raise ValueError("missing ] in expression")
            i += 1
            words.append((letter, code[start:i]))
            continue
        match = PARAM_RE.match(code, i) or NUMBER_RE.match(code, i)
        if not match:
            raise ValueError(f"the word {letter} has an invalid value")
        words.append((letter, match.group()))
        i = match.end()
    return words


def format_number(value):
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _word_text(letter, value):
    return f"{letter}{value}"


def flatten_arc(start, center_offset, end, mode):
    """Return the points of an arc after the start point, for preview and bounds."""
    cx, cy = start[0] + center_offset[0], start[1] + center_offset[1]
    radius = math.hypot(start[0] - cx, start[1] - cy)
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    a1 = math.atan2(end[1] - cy, end[0] - cx)
    if mode == 3:
        sweep = a1 - a0
        if sweep <= 1e-9:
            sweep += 2 * math.pi
    else:
        sweep = a1 - a0
        if sweep >= -1e-9:
            sweep -= 2 * math.pi
    if radius < 1e-9:
        return [end]
    ratio = max(min(1 - ARC_CHORD_TOLERANCE / radius, 1.0), -1.0)
    step = max(2 * math.acos(ratio), math.radians(1.0))
    count = max(int(abs(sweep) / step) + 1, 2)
    points = [(cx + radius * math.cos(a0 + sweep * k / count),
               cy + radius * math.sin(a0 + sweep * k / count)) for k in range(1, count)]
    points.append(end)
    return points


def _r_to_center(start, end, radius, mode, line):
    """Convert an R-format arc to an I/J offset."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    chord = math.hypot(dx, dy)
    if chord < 1e-9:
        raise GCodeError("R arc with the same start and end point", line)
    half = chord / 2
    if abs(radius) < half - 1e-6:
        raise GCodeError("the R arc's radius is too small for the distance", line)
    height = math.sqrt(max(radius * radius - half * half, 0.0))
    # A positive R takes the short way round, a negative R the long way.
    side = height if (mode == 3) == (radius > 0) else -height
    return dx / 2 - side * dy / chord, dy / 2 + side * dx / chord


class Program:
    """A parsed G-code file."""

    def __init__(self, name, text):
        self.name = name
        self.text = text
        self.ops = []
        self.warnings = []
        self.paths = []
        self.has_materials = False
        self._parse()
        self._mark_cuts()
        self._build_paths()
        self.bounds = self._bounds()
        self.center = ((self.bounds[0] + self.bounds[2]) / 2, (self.bounds[1] + self.bounds[3]) / 2)

    # ------------------------------------------------------------------ parsing

    def _parse(self):
        self.scale = None
        self.dist_incremental = False
        self.arc_absolute = False
        self.motion = None
        self.pos = (0.0, 0.0)
        self.torch = False
        self.params = {}
        self.seen_first_move = False
        for number, raw in enumerate(self.text.splitlines(), 1):
            self._parse_line(raw.strip(), number)
        if self.scale is None:
            self.warnings.append("The file has no G20/G21 - read as millimetres.")
        if not any(op.kind == "move" for op in self.ops):
            raise GCodeError("The file has no XY moves.")

    def _unit(self):
        return self.scale if self.scale is not None else 1.0

    def _parse_line(self, line, number):
        if not line or line == "%":
            return
        code, comments = split_comments(line)
        if not code:
            self._parse_comment_line(line, number)
            return
        if code.startswith("/"):
            raise GCodeError("Block delete (/) is not supported.", number)
        if re.match(r"^[oO]\s*(\d|<)", code):
            raise GCodeError("O-words (subroutines and loops) are not supported. Export the file "
                             "without subroutines from the CAM program.", number)
        if code.startswith("#"):
            self._parse_assignment(code, comments, number)
            return
        try:
            words = tokenize(code)
        except ValueError as error:
            raise GCodeError(f"{error}: {code}", number)
        self._parse_block(words, comments, number)

    def _parse_comment_line(self, line, number):
        lowered = line.lower().replace(" ", "")
        if lowered.startswith(";qtplasmacfilteredg-codefile"):
            # Exported, it would go through the filter again: holes would get a second overburn.
            raise GCodeError("The file has already been through QtPlasmaC's filter, which has added "
                             "overburn and speed changes at holes. Load the original file "
                             "instead.", number)
        if lowered.startswith(";conversationalblock"):
            # The filter requires this on line 1, which is not where it would end up.
            self.warnings.append(f"Line {number}: the QtPlasmaC header was removed.")
            return
        match = re.match(r"^\(o=(\d)", lowered)
        if match:
            kind = "temp_material" if match.group(1) == "0" else "lib_material"
            self.has_materials = True
            self.ops.append(Op(kind, line))
            return
        self.ops.append(Op("raw", line))

    def _parse_assignment(self, code, comments, number):
        match = ASSIGN_RE.match(code)
        if not match:
            raise GCodeError(f"Cannot read the parameter line: {code}", number)
        key = normalize_param(match.group(1))
        value_text = match.group(2)
        try:
            value = evaluate(value_text, self.params)
        except Unresolved:
            self.params.pop(key, None)
            value = None
        except (ValueError, ArithmeticError) as error:
            raise GCodeError(str(error), number)
        if value is not None:
            self.params[key] = value
        if value is not None and key in LENGTH_PARAMS and self._unit() != 1.0:
            value_text = format_number(value * self._unit())
        text = " ".join([f"{match.group(1)} = {value_text}"] + comments)
        self.ops.append(Op("raw", text))

    def _value(self, word, number):
        try:
            return evaluate(word[1], self.params)
        except Unresolved as error:
            raise GCodeError(f"The coordinate {word[0]}{word[1]} depends on {error}, which only "
                             "LinuxCNC knows. The file cannot be moved safely.", number)
        except (ValueError, ArithmeticError) as error:
            raise GCodeError(str(error), number)

    def _parse_block(self, words, comments, number):
        axes, kept, new_motion, machine_coords = {}, [], None, False
        position_mode_lines = []
        for word in words:
            letter, value = word
            if letter == "N":
                continue
            if letter in "XYIJR":
                axes[letter] = word
                continue
            if letter in "ABCUVW":
                raise GCodeError(f"The {letter} axis is not supported (only X and Y can be moved).", number)
            if letter == "G":
                action = self._parse_g(value, number)
                if action == "motion":
                    new_motion = int(float(value))
                    kept.append(word)
                elif action == "machine":
                    machine_coords = True
                    kept.append(word)
                elif action == "dist":
                    position_mode_lines.append(f"G{value}")
                elif action == "keep":
                    kept.append(word)
                elif action == "drop_line":
                    self.warnings.append(f"Line {number}: G{value} was removed.")
                    return
                continue
            if letter == "M":
                m = self._number_or_none(value)
                if m in (2, 30, 60):
                    continue
                if m == 3:
                    self.torch = True
                elif m == 5:
                    self.torch = False
            kept.append(word)

        for text in position_mode_lines:
            self.ops.append(Op("raw", text))

        if new_motion is not None:
            self.motion = new_motion

        is_move = any(k in axes for k in "XYIJR")
        if machine_coords and is_move:
            self.warnings.append(f"Line {number}: a G53 move in XY was removed.")
            return
        if is_move:
            self._add_move(axes, kept, comments, number)
            return
        self._add_plain_block(kept, comments)

    def _parse_g(self, value, number):
        g = self._number_or_none(value)
        if g is None:
            raise GCodeError(f"The G-code G{value} must be a number.", number)
        g = round(g, 2)
        if g in (0, 1, 2, 3):
            return "motion"
        if g == 20 or g == 21:
            scale = MM_PER_INCH if g == 20 else 1.0
            if self.scale is not None and self.scale != scale:
                raise GCodeError("The file mixes G20 and G21.", number)
            self.scale = scale
            return "unit"
        if g in (90, 91):
            self.dist_incremental = g == 91
            return "dist"
        if g in (90.1, 91.1):
            self.arc_absolute = g == 90.1
            return "unit"
        if g == 53:
            return "machine"
        if g in (28, 28.1, 30, 30.1):
            return "drop_line"
        if g == 92 or g == 52:
            raise GCodeError(f"G{value} (offset) is not supported - QtPlasmaC does not allow G92 "
                             "and the placement would be wrong.", number)
        if g == 10:
            raise GCodeError("G10 changes coordinate systems and is not supported.", number)
        if g in (18, 19):
            raise GCodeError(f"G{value}: only the XY plane (G17) is supported.", number)
        if g in (5, 5.1, 5.2, 5.3, 7, 8, 33, 33.1, 38.2, 38.3, 38.4, 38.5, 73, 76, 81, 82, 83, 84, 85, 86, 87, 88, 89):
            raise GCodeError(f"G{value} is not supported when nesting.", number)
        return "keep"

    @staticmethod
    def _number_or_none(value):
        try:
            return float(value)
        except ValueError:
            return None

    def _scaled_extra(self, word, kept):
        """Text for a kept word, converting lengths when the file is in inches."""
        letter, value = word
        number = self._number_or_none(value)
        if self._unit() != 1.0 and number is not None:
            g_words = [self._number_or_none(w[1]) for w in kept if w[0] == "G"]
            is_length = (letter == "F"
                         or (letter == "D" and any(g in (41.1, 42.1) for g in g_words))
                         or (letter in "PQ" and 64 in g_words)
                         or letter == "Z")
            if is_length:
                return _word_text(letter, format_number(number * self._unit()))
        return _word_text(letter, value)

    def _add_plain_block(self, kept, comments):
        if not kept and not comments:
            return
        texts = [self._scaled_extra(w, kept) for w in kept]
        codes = [(w[0], self._number_or_none(w[1])) for w in kept]
        text = " ".join(texts + comments)
        if len(codes) == 2 and codes[0] == ("M", 190) and codes[1][0] == "P" and codes[1][1] is not None:
            self.has_materials = True
            self.ops.append(Op("material", text))
        elif codes[:1] == [("M", 66)] and ("P", 3) in codes and ("L", 3) in codes:
            self.ops.append(Op("material_wait", text))
        else:
            self.ops.append(Op("raw", text))

    def _add_move(self, axes, kept, comments, number):
        mode = self.motion
        if mode is None:
            raise GCodeError("Koordinater utan aktiv G0/G1/G2/G3.", number)
        unit = self._unit()
        values = {k: self._value(w, number) * unit for k, w in axes.items()}
        start = self.pos
        if not self.seen_first_move and not ("X" in values and "Y" in values) and not self.dist_incremental:
            self.warnings.append(f"Line {number}: the first move has no X or Y - a missing axis is read as 0.")
        if self.dist_incremental:
            end = (start[0] + values.get("X", 0.0), start[1] + values.get("Y", 0.0))
        else:
            end = (values.get("X", start[0]), values.get("Y", start[1]))

        center = None
        if mode in (2, 3):
            if "R" in values:
                center = _r_to_center(start, end, values["R"], mode, number)
            elif "I" in values or "J" in values:
                if self.arc_absolute:
                    center = (values.get("I", 0.0) - start[0], values.get("J", 0.0) - start[1])
                else:
                    center = (values.get("I", 0.0), values.get("J", 0.0))
            else:
                raise GCodeError("Arc without I, J or R.", number)
            for w in kept:
                if w[0] == "P" and self._number_or_none(w[1]) not in (None, 1):
                    raise GCodeError("Arcs with several turns (P) are not supported.", number)
        elif any(k in axes for k in "IJR"):
            raise GCodeError("I, J or R on a straight move.", number)

        motion_words = {("G", w[1]) for w in kept if w[0] == "G" and self._number_or_none(w[1]) in (0, 1, 2, 3)}
        extras = [self._scaled_extra(w, kept) for w in kept
                  if (w[0], w[1]) not in motion_words and not (w[0] == "P" and mode in (2, 3))]
        self.ops.append(Op("move", move=Move(mode, start, end, center, self.dist_incremental,
                                             self.torch, extras, " ".join(comments))))
        self.pos = end
        self.seen_first_move = True

    # ---------------------------------------------------------------- geometry

    def _mark_cuts(self):
        moves = [op.move for op in self.ops if op.kind == "move"]
        has_torch = any(m.torch for m in moves)
        for m in moves:
            m.cut = m.mode != 0 and (m.torch or not has_torch)
        if not has_torch:
            self.warnings.append("The file has no M3 - all G1/G2/G3 are shown as cuts, but "
                                 "the machine will not light the torch.")
        if not any(m.cut for m in moves):
            raise GCodeError("The file has no cutting moves.")
        # Parking moves after the final cut belong to the file, not to the part.
        last_cut = max(i for i, op in enumerate(self.ops) if op.kind == "move" and op.move.cut)
        self.ops = [op for i, op in enumerate(self.ops) if not (op.kind == "move" and i > last_cut)]
        self.ends_with_torch_on = self.torch

    def _build_paths(self):
        current = None
        previous_torch = None
        for op in self.ops:
            if op.kind != "move":
                continue
            m = op.move
            kind = "cut" if m.cut else ("rapid" if m.mode == 0 else "feed")
            points = flatten_arc(m.start, m.center, m.end, m.mode) if m.center is not None else [m.end]
            continuous = current is not None and current.kind == kind and current.points[-1] == m.start
            if not continuous or m.torch != previous_torch:
                current = PathPart(kind, [m.start])
                self.paths.append(current)
            current.points.extend(points)
            previous_torch = m.torch
        # The very first rapid starts at an unknown machine position.
        if self.paths and self.paths[0].kind == "rapid":
            self.paths[0].points.pop(0)
            if len(self.paths[0].points) < 2:
                self.paths.pop(0)

    def _bounds(self):
        points = [p for path in self.paths if path.kind == "cut" for p in path.points]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return min(xs), min(ys), max(xs), max(ys)


def load_program(path):
    """Read and parse a G-code file from disk."""
    import os
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return Program(os.path.basename(path), text)
