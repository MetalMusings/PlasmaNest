# SPDX-License-Identifier: GPL-2.0-or-later
"""Qt user interface. The scene uses G-code coordinates in mm; the view flips Y."""

import math
import os
import signal
import sys
import time
from dataclasses import replace

from PyQt5 import sip
from PyQt5.QtCore import (QEvent, QLocale, QObject, QPoint, QPointF, QRectF, QSize, QSocketNotifier, Qt, QTimer,
                          pyqtSignal)
from PyQt5.QtGui import (QBrush, QColor, QFontMetrics, QKeySequence, QMouseEvent, QPainter, QPainterPath, QPainterPathStroker,
                         QPen, QPolygonF, QTransform)
from PyQt5.QtWidgets import (QAbstractItemView, QAction, QApplication, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                             QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsSimpleTextItem,
                             QGraphicsView, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
                             QMainWindow, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget,
                             QTableWidgetItem, QToolTip, QVBoxLayout, QWidget)

from . import live, machine
from .autonest import autonest, group_figures
from .gcode import GCodeError, Program, format_number, load_program
from .nest import (Placement, Sheet, closed_loops, export_gcode, inside_first, load_project, material_warning,
                   rotation, save_project)
from .materials import available_materials, machine_work_area
from .theme import qtplasmac_colors, stylesheet
from .shapes import (OVERCUT, SMALL_HOLE, SMALL_HOLE_SPEED, Circle, HoleCircle, Polygon, Rectangle, Slot,
                     shape_of)

APP_NAME = "PlasmaNest"
PART_GAP = 10.0        # mm between figures placed automatically
FREE_SPOT_GRID = 24    # extra positions per axis tried on a polygon sheet
CLOSE_POLYGON_RADIUS = 10  # pixels from the first corner that closes a drawn polygon
NUDGE_STEP = 1.0       # mm per arrow key press
NUDGE_STEP_LARGE = 10.0
HANDLE_DISTANCE = 42   # pixels from the figure to the rotation handle
ROTATE_SNAP = 15.0     # degrees with Ctrl held
ROTATE_MIN_RADIUS = 15 # pixels from the pivot; closer than this the angle is too jumpy
DEFAULT_SHEET = (3000.0, 1500.0)  # mm, without LinuxCNC to give the work area
RESIZE_REACH = 6  # pixels from a drawn figure's outline where dragging resizes it

# QtPlasmaC (2.9) keeps the shapes accepted in CONVERSATIONAL here; SEND loads this file.
CONVERSATIONAL_FILE = "/tmp/qtplasmac/backup.ngc"

# Where unsaved work goes when QtPlasmaC ends an embedded PlasmaNest.
AUTOSAVE_FILE = os.path.join(os.path.dirname(machine.POINTS_FILE), "autosave.pnest")

# QtPlasmaC's user button "latest-file <dir>" opens the newest .ngc in this directory.
NC_FILES = os.path.join(os.path.expanduser("~"), "linuxcnc", "nc_files")
EXPORT_DIR_NAME = "plasmanest"

COLOR_CUT = QColor("#1565c0")
COLOR_SELECTED = QColor("#00a152")
COLOR_OVERLAP = QColor("#ef6c00")
COLOR_OUTSIDE = QColor("#d32f2f")
COLOR_RAPID = QColor("#7a7a7a")
COLOR_SHEET = QColor("#e4e7ea")
COLOR_SHEET_EDGE = QColor("#37474f")
COLOR_GRID = QColor("#ccd2d7")
COLOR_VIEW = QColor("#90a4ae")
COLOR_ORIGIN = QColor("#6a1b9a")
COLOR_DRAFT = QColor("#c2185b")
COLOR_TEXT = QColor("#263238")       # text drawn in the view
COLOR_LABEL_BG = QColor(255, 255, 255, 230)
COLOR_HELP = QColor("#607d8b")


def blend(a, b, amount):
    """a moved amount (0..1) of the way towards b."""
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * amount), round(a.green() + (b.green() - a.green()) * amount),
                  round(a.blue() + (b.blue() - a.blue()) * amount))


def apply_theme(app, colors):
    """QtPlasmaC's look: its style sheet for the widgets, and its colours in the view.
    Call before the main window is built: the view reads these colours as it draws."""
    global COLOR_CUT, COLOR_SELECTED, COLOR_OVERLAP, COLOR_OUTSIDE, COLOR_RAPID, COLOR_SHEET
    global COLOR_SHEET_EDGE, COLOR_GRID, COLOR_VIEW, COLOR_ORIGIN, COLOR_DRAFT, COLOR_TEXT, COLOR_LABEL_BG, COLOR_HELP
    app.setStyleSheet(stylesheet(colors))
    fg, bg, alt = QColor(colors["Foreground"]), QColor(colors["Background"]), QColor(colors["Background Alt"])
    COLOR_VIEW = QColor(colors["Preview"])
    COLOR_SHEET = alt
    COLOR_SHEET_EDGE = QColor(colors["Frames"])
    COLOR_GRID = blend(alt, fg, 0.12)
    COLOR_CUT = fg
    COLOR_TEXT = fg
    COLOR_LABEL_BG = QColor(bg.red(), bg.green(), bg.blue(), 235)
    COLOR_HELP = QColor(colors["Disabled"])
    # The meanings stay, in shades that show on a dark ground.
    COLOR_SELECTED = QColor("#33d17a")
    COLOR_OVERLAP = QColor("#ff9f1a")
    COLOR_OUTSIDE = QColor(colors["Estop"])
    COLOR_RAPID = QColor("#8a8a80")
    COLOR_ORIGIN = QColor("#c77dff")
    COLOR_DRAFT = QColor("#ff4fa0")


def cosmetic_pen(color, width=1.0, style=Qt.SolidLine):
    pen = QPen(color, width, style)
    pen.setCosmetic(True)
    return pen


def holes_within(part, region, other):
    """True for holes drawn in PlasmaNest lying wholly in another figure's material: holes in a part."""
    shape = shape_of(part.placement.program)
    return shape is not None and shape.inside and other.contains(region)


def plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def format_mm(value):
    return f"{value:.0f}" if abs(value - round(value)) < 1e-9 else f"{value:.1f}"


def normalize_angle(angle):
    angle = math.fmod(angle, 360.0)
    return angle + 360.0 if angle < 0 else angle


class SheetItem(QGraphicsItem):
    """The steel sheet in G-code coordinates, with a grid aligned to the origin."""

    GRID = 100.0

    def __init__(self, sheet):
        super().__init__()
        self.setZValue(-10)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.set_sheet(sheet)

    def set_sheet(self, sheet):
        self.prepareGeometryChange()
        self.sheet = sheet
        self.outline = QPainterPath()
        self.outline.addPolygon(QPolygonF([QPointF(x, y) for x, y in sheet.corners]))
        self.outline.closeSubpath()
        self.update()

    def boundingRect(self):
        return self.outline.boundingRect().adjusted(-2, -2, 2, 2)

    def paint(self, painter, option, widget=None):
        x0, y0, x1, y1 = self.sheet.bounds
        painter.fillPath(self.outline, COLOR_SHEET)
        painter.save()
        painter.setClipPath(self.outline)
        painter.setPen(cosmetic_pen(COLOR_GRID))
        x = math.ceil(x0 / self.GRID) * self.GRID
        while x < x1:
            painter.drawLine(QPointF(x, y0), QPointF(x, y1))
            x += self.GRID
        y = math.ceil(y0 / self.GRID) * self.GRID
        while y < y1:
            painter.drawLine(QPointF(x0, y), QPointF(x1, y))
            y += self.GRID
        painter.restore()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(cosmetic_pen(COLOR_SHEET_EDGE, 2.0))
        painter.drawPath(self.outline)


class OriginItem(QGraphicsItem):
    """Cross at G-code X0 Y0, since a polygon sheet need not start there."""

    SIZE = 12.0

    def __init__(self):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setToolTip("G-code X0 Y0")
        self.setZValue(-5)

    def boundingRect(self):
        return QRectF(-self.SIZE - 1, -self.SIZE - 1, 2 * self.SIZE + 2, 2 * self.SIZE + 2)

    def paint(self, painter, option, widget=None):
        painter.setPen(cosmetic_pen(COLOR_ORIGIN, 2.0))
        painter.drawLine(QPointF(-self.SIZE, 0), QPointF(self.SIZE, 0))
        painter.drawLine(QPointF(0, -self.SIZE), QPointF(0, self.SIZE))


class RotateHandle(QGraphicsItem):
    """Round grip above a selected figure; drag it to rotate."""

    RADIUS = 8.0

    def __init__(self, part):
        super().__init__(part)
        self.part = part
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setCursor(Qt.CrossCursor)
        self.setToolTip("Drag to rotate. Hold Ctrl for 15° steps.")
        self.setZValue(5)
        self.grab_angle = 0.0
        self.start_angle = 0.0

    def boundingRect(self):
        r = self.RADIUS + 2
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(cosmetic_pen(QColor("white"), 2.0))
        painter.setBrush(QBrush(COLOR_SELECTED))
        painter.drawEllipse(QPointF(0, 0), self.RADIUS, self.RADIUS)

    def _pointer_angle(self, scene_pos):
        center = self.part.pos()
        return math.degrees(math.atan2(scene_pos.y() - center.y(), scene_pos.x() - center.x()))

    def mousePressEvent(self, event):
        self.grab_angle = self._pointer_angle(event.scenePos())
        self.start_angle = self.part.placement.angle
        event.accept()

    def mouseMoveEvent(self, event):
        angle = self.start_angle + self._pointer_angle(event.scenePos()) - self.grab_angle
        if event.modifiers() & Qt.ControlModifier:
            angle = round(angle / ROTATE_SNAP) * ROTATE_SNAP
        self.part.set_angle(angle)
        self.part.window.schedule_refresh()

    def mouseReleaseEvent(self, event):
        self.part.window.commit()


class PartItem(QGraphicsItem):
    """A placed figure. Local coordinates are centred on the program's bounding box."""

    def __init__(self, placement, window):
        super().__init__()
        self.placement = placement
        self.window = window
        self.state = "ok"
        self.show_rapids = True
        self.on_sheet_cache = (None, True)
        self.drawn_shape = shape_of(placement.program)  # the parts are remade when it changes
        # Construction lines that are not cut: a hole circle's circle through the hole centres.
        self.guide_path = QPainterPath()
        if isinstance(self.drawn_shape, HoleCircle):
            centre = QPointF(self.drawn_shape.cx - placement.program.center[0],
                             self.drawn_shape.cy - placement.program.center[1])
            self.guide_path.addEllipse(centre, self.drawn_shape.diameter / 2, self.drawn_shape.diameter / 2)
        program = placement.program
        drawn = self.drawn_shape
        cx, cy = program.center
        self.cut_path = QPainterPath()
        self.fill_path = QPainterPath()  # the closed contours: the material, without lead-ins
        self.fill_path.setFillRule(Qt.OddEvenFill)
        self.rapid_path = QPainterPath()
        for path in program.paths:
            points = [QPointF(x - cx, y - cy) for x, y in path.points]
            if path.kind == "cut":
                self.cut_path.addPolygon(QPolygonF(points))
                if not (isinstance(drawn, Polygon) and not drawn.closed):  # a line has no inside
                    for start, end in closed_loops(path.points):
                        self.fill_path.addPolygon(QPolygonF(points[start:end + 1]))
            else:
                self.rapid_path.addPolygon(QPolygonF(points))
        self.rect = self.cut_path.boundingRect()

        stroker = QPainterPathStroker()
        stroker.setWidth(6.0)
        self.stroke_path = stroker.createStroke(self.cut_path)
        self.hit_shape = QPainterPath(self.fill_path)
        self.hit_shape.addPath(self.stroke_path)

        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
                      | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemIsFocusable)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip(program.name)

        self.handle = RotateHandle(self)
        self.handle.setPos(0, self.rect.bottom())
        self.handle.setVisible(False)

        self.label = QGraphicsSimpleTextItem(self)
        self.label.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.label.setAcceptedMouseButtons(Qt.NoButton)
        self.label.setBrush(QBrush(COLOR_TEXT))

        self.setPos(placement.x, placement.y)
        self.setRotation(placement.angle)
        self.set_mirror(placement.mirror)

    def set_number(self, number):
        self.label.setText(str(number))
        # The label is not mirrored, but where it is put is.
        half = self.label.boundingRect().width() / 2
        self.label.setPos(half if self.placement.mirror else -half, 0)

    def set_angle(self, angle):
        self.placement.angle = normalize_angle(angle)
        self.setRotation(self.placement.angle)
        self._mirror_transform()

    def set_mirror(self, mirror):
        self.placement.mirror = bool(mirror)
        self._mirror_transform()
        if self.label.text():
            self.set_number(int(self.label.text()))

    def _mirror_transform(self):
        """Placement.map_point mirrors and then turns, but Qt turns before the item's transform.
        Mirroring after the turn is a flip across the figure's own Y axis, turned by the angle."""
        if not self.placement.mirror:
            self.setTransform(QTransform())
            return
        c, s = rotation(2 * self.placement.angle)
        self.setTransform(QTransform(-c, -s, -s, c, 0.0, 0.0))

    def set_center(self, x, y):
        self.setPos(x, y)

    def set_state(self, state):
        if state != self.state:
            self.state = state
            self.update()

    def set_show_rapids(self, show):
        self.show_rapids = show
        self.update()

    def scene_region(self):
        return self.sceneTransform().map(self.fill_path)

    def on_sheet(self, sheet):
        # A polygon check walks every cut segment, and refresh runs for every figure while dragging.
        key = (self.placement.x, self.placement.y, self.placement.angle, self.placement.mirror, sheet)
        if self.on_sheet_cache[0] != key:
            self.on_sheet_cache = (key, sheet.contains_placement(self.placement))
        return self.on_sheet_cache[1]

    def boundingRect(self):
        rect = self.cut_path.boundingRect().united(self.rapid_path.boundingRect()).united(
            self.guide_path.boundingRect())
        return rect.adjusted(-2, -2, 2, 2)

    def shape(self):
        return self.hit_shape

    # Hit tests use the fill and the stroke round the cut separately. In one odd-even path the
    # stroke cuts a gap in the fill where they overlap, as round a lead-in from a hole's centre,
    # and the figure cannot be grabbed there. Uniting the paths instead takes seconds for a large
    # file. The view tests a pixel's square with collidesWithPath, the scene a point with contains.

    def contains(self, point):
        return self.fill_path.contains(point) or self.stroke_path.contains(point)

    def collidesWithPath(self, path, mode=Qt.IntersectsItemShape):
        if mode == Qt.IntersectsItemShape:
            return self.fill_path.intersects(path) or self.stroke_path.intersects(path)
        if mode == Qt.ContainsItemShape:
            return path.contains(self.fill_path) and path.contains(self.stroke_path)
        return super().collidesWithPath(path, mode)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.placement.x, self.placement.y = value.x(), value.y()
            self.window.schedule_refresh()
        elif change == QGraphicsItem.ItemSelectedHasChanged:
            self.setZValue(1 if value else 0)
        return super().itemChange(change, value)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        selected = self.isSelected()
        color = {"ok": COLOR_CUT, "overlap": COLOR_OVERLAP, "outside": COLOR_OUTSIDE,
                 "off": COLOR_RAPID}[self.state]
        if selected and self.state in ("ok", "off"):
            color = COLOR_SELECTED
        fill = QColor(color)
        fill.setAlpha((70 if selected else 35) // (3 if self.state == "off" else 1))
        painter.fillPath(self.fill_path, fill)
        if self.show_rapids and not self.rapid_path.isEmpty():
            painter.setPen(cosmetic_pen(COLOR_RAPID, 1.0, Qt.DashLine))
            painter.drawPath(self.rapid_path)
        if not self.guide_path.isEmpty():
            painter.setPen(cosmetic_pen(color, 1.0, Qt.DotLine))
            painter.drawPath(self.guide_path)
        # Not cut: dashed, so it stays visible on the sheet without looking like part of the job.
        style = Qt.DashLine if self.state == "off" else Qt.SolidLine
        painter.setPen(cosmetic_pen(color, 2.0 if selected else 1.4, style))
        painter.drawPath(self.cut_path)


class DraftLabel(QGraphicsItem):
    """Text beside the pointer while drawing: always the same size and readable on any background."""

    OFFSET = QPointF(14, 14)  # pixels right of and below the pointer
    PADDING = 4

    def __init__(self):
        super().__init__()
        self.text = ""
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setZValue(20)
        self.setVisible(False)

    def set_text(self, text, scene_pos):
        self.prepareGeometryChange()
        self.text = text
        self.setPos(scene_pos)
        self.setVisible(bool(text))
        self.update()

    def _rect(self):
        metrics = QFontMetrics(self.scene().font() if self.scene() else QApplication.font())
        size = metrics.size(0, self.text)
        return QRectF(self.OFFSET.x(), self.OFFSET.y(), size.width() + 2 * self.PADDING,
                      size.height() + 2 * self.PADDING)

    def boundingRect(self):
        return self._rect().adjusted(-1, -1, 1, 1)

    def paint(self, painter, option, widget=None):
        rect = self._rect()
        painter.setPen(QPen(COLOR_DRAFT, 1))
        painter.setBrush(QBrush(COLOR_LABEL_BG))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QPen(COLOR_TEXT))
        painter.drawText(rect, Qt.AlignCenter, self.text)


class NestView(QGraphicsView):
    """Zoom with the wheel, pan with the middle button or by right-dragging empty space.

    Right-dragging a figure rotates it, or the whole selection if it is part of one.
    """

    def __init__(self, scene, window):
        super().__init__(scene)
        self.window_ = window
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setRubberBandSelectionMode(Qt.IntersectsItemShape)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setBackgroundBrush(QBrush(COLOR_VIEW))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.scale(1, -1)
        self.pan_from = None
        self.rotation_drag = None
        self.resize_drag = None
        self.grip_cursor = False
        self.drawing = None
        self.drawing_target = "sheet"  # or "polygon": a figure clicked out like the sheet
        self.draft = QGraphicsPathItem()
        self.draft.setPen(cosmetic_pen(COLOR_DRAFT, 2.0))
        self.draft.setAcceptedMouseButtons(Qt.NoButton)
        self.draft.setZValue(10)
        scene.addItem(self.draft)
        self.label = DraftLabel()
        scene.addItem(self.label)
        self.shape_mode = None   # "rectangle" while a new figure is drawn, "leadin" while placing one
        self.leadin_part = None
        self.leadin_choice = None
        self.shape_start = None  # its first corner, in scene mm
        self.shape_press = None  # view position of that press, to tell a drag from a click

    # ------------------------------------------------------------ polygon drawing

    def start_drawing(self, target="sheet"):
        if self.shape_mode is not None:
            self.stop_shape()
        self.scene().clearSelection()
        self.drawing_target = target
        self.drawing = []
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self.update_draft(None)

    def stop_drawing(self):
        self.drawing = None
        self.unsetCursor()
        self.draft.setPath(QPainterPath())
        if self.drawing_target == "polygon":
            self.drawing_target = "sheet"
            self.window_.update_shape_panel()

    def draft_point(self, pos, modifiers):
        """Scene position of a corner: whole mm, with Shift straight from the previous corner."""
        scene_pos = self.mapToScene(pos)
        x, y = round(scene_pos.x()), round(scene_pos.y())
        if modifiers & Qt.ShiftModifier and self.drawing:
            last = self.drawing[-1]
            if abs(x - last[0]) >= abs(y - last[1]):
                y = last[1]
            else:
                x = last[0]
        return float(x), float(y)

    def update_draft(self, cursor):
        path = QPainterPath()
        points = list(self.drawing)
        if cursor is not None:
            points.append(cursor)
        if points:
            path.addPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        self.draft.setPath(path)

    def near_first_corner(self, pos):
        if len(self.drawing) < 3:
            return False
        first = self.mapFromScene(QPointF(*self.drawing[0]))
        return math.hypot(pos.x() - first.x(), pos.y() - first.y()) <= CLOSE_POLYGON_RADIUS

    def finish_drawing(self, closed=True):
        if self.drawing_target == "polygon":
            self.finish_polygon(closed)
            return
        try:
            sheet = Sheet(self.drawing)
        except ValueError as error:
            # Keep the corners: the last ones can be taken back with Backspace.
            self.window_.sheet_info.setText(f"<b style='color:{COLOR_OUTSIDE.name()}'>{error}</b>")
            return
        self.stop_drawing()
        self.window_.set_sheet(sheet)

    def finish_polygon(self, closed):
        try:
            program = self.window_.new_polygon(self.drawing, closed).program()
        except ValueError as error:
            # Keep the points: the last ones can be taken back with Backspace.
            self.window_.statusBar().showMessage(str(error), 10000)
            return
        self.stop_drawing()
        self.window_.add_shape(program)

    def drawing_key(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish_drawing(closed=self.drawing_target == "sheet")  # a polygon figure: open
        elif event.key() == Qt.Key_Escape:
            self.stop_drawing()
            self.window_.update_sheet_panel()
        elif event.key() == Qt.Key_Backspace:
            if self.drawing:
                self.drawing.pop()
            self.update_draft(None)
        else:
            return False
        return True

    # -------------------------------------------------------------- new figures

    SHAPE_HELP = {
        "rectangle": "Press at the first corner and drag to the opposite corner, or click both. Esc cancels.",
        "circle": "Press at the centre and drag out to the edge, or click the centre and then the edge. "
                  "Esc cancels.",
        "slot": "Press at a corner and drag to the opposite corner, or click both. The slot fills the box "
                "along its longer side. Esc cancels.",
        "hole circle": "Press at the centre and drag out to the hole centres, or click the centre and then "
                       "a hole centre. Esc cancels.",
    }

    def start_shape(self, kind):
        self.stop_drawing()
        self.scene().clearSelection()
        self.shape_mode = kind
        self.shape_start = None
        self.shape_press = None
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self.window_.statusBar().showMessage(self.SHAPE_HELP[kind])
        self.window_.update_shape_panel()

    def start_rectangle(self):
        self.start_shape("rectangle")

    def start_circle(self):
        self.start_shape("circle")

    def start_hole_circle(self):
        self.start_shape("hole circle")

    def start_slot(self):
        self.start_shape("slot")

    def stop_shape(self):
        self.shape_mode = None
        self.shape_start = None
        self.shape_press = None
        self.unsetCursor()
        self.draft.setPath(QPainterPath())
        self.label.set_text("", QPointF())
        self.window_.update_shape_panel()

    def snap(self, pos):
        """Whole mm, as when drawing the sheet."""
        scene_pos = self.mapToScene(pos)
        return float(round(scene_pos.x())), float(round(scene_pos.y()))

    def circle_diameter(self, pos):
        """Whole mm, measured from the centre to the pointer."""
        x0, y0 = self.shape_start
        scene_pos = self.mapToScene(pos)
        return float(round(2 * math.hypot(scene_pos.x() - x0, scene_pos.y() - y0)))

    def update_shape(self, pos):
        if self.shape_mode == "leadin":
            self.update_leadin(pos)
            return
        x, y = self.snap(pos)
        if self.shape_start is None:
            self.draft.setPath(QPainterPath())
            self.label.set_text(f"X {format_mm(x)}   Y {format_mm(y)}", QPointF(x, y))
            return
        x0, y0 = self.shape_start
        path = QPainterPath()
        if self.shape_mode in ("circle", "hole circle"):
            diameter = self.circle_diameter(pos)
            path.addEllipse(QPointF(x0, y0), diameter / 2, diameter / 2)
            path.moveTo(x0, y0)
            path.lineTo(self.mapToScene(pos))
            text = f"Ø {format_mm(diameter)} mm"
            if self.shape_mode == "hole circle":
                shape = self.window_.new_hole_circle(x0, y0, diameter)
                for x, y in shape.centres():
                    path.addEllipse(QPointF(x, y), shape.hole_diameter / 2, shape.hole_diameter / 2)
                text += f", {shape.holes} × Ø {format_mm(shape.hole_diameter)}"
        elif self.shape_mode == "slot":
            w, h = abs(x - x0), abs(y - y0)
            path.addRoundedRect(QRectF(QPointF(min(x0, x), min(y0, y)), QPointF(max(x0, x), max(y0, y))),
                                min(w, h) / 2, min(w, h) / 2)
            text = f"{format_mm(max(w, h))} × {format_mm(min(w, h))} mm"
        else:
            path.addRect(QRectF(QPointF(min(x0, x), min(y0, y)), QPointF(max(x0, x), max(y0, y))))
            text = f"{format_mm(abs(x - x0))} × {format_mm(abs(y - y0))} mm"
        self.draft.setPath(path)
        self.label.set_text(text, self.mapToScene(pos))

    def finish_shape(self, pos):
        x0, y0 = self.shape_start
        angle = 0.0
        try:
            if self.shape_mode == "circle":
                shape = self.window_.new_circle(x0, y0, self.circle_diameter(pos))
            elif self.shape_mode == "hole circle":
                shape = self.window_.new_hole_circle(x0, y0, self.circle_diameter(pos))
            elif self.shape_mode == "slot":
                shape, angle = self.window_.new_slot(x0, y0, *self.snap(pos))
            else:
                shape = self.window_.new_rectangle(x0, y0, *self.snap(pos))
            program = shape.program()
        except ValueError:
            return False  # too small: keep waiting for the second point
        self.stop_shape()
        self.window_.add_shape(program, angle)
        return True

    def start_leadin(self, part):
        """Point at or near a drawn figure to put its lead-in at the nearest place on its outline."""
        self.stop_drawing()
        self.shape_mode = "leadin"
        self.leadin_part = part
        self.leadin_choice = None
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self.window_.statusBar().showMessage("Point at or near the figure and click where the lead-in should be. "
                                             "Esc cancels.")
        self.window_.update_shape_panel()

    LEADIN_REACH = 30  # pixels from the outline that still count as pointing at it

    def leadin_at(self, pos):
        """Lead-in position on the figure nearest a view position, or None if too far away."""
        placement = self.leadin_part.placement
        shape = shape_of(placement.program)
        scene = self.mapToScene(pos)
        position, distance = shape.nearest(placement.unmap_point((scene.x(), scene.y())))
        if distance * abs(self.transform().m11()) > self.LEADIN_REACH:
            return None
        return position

    def update_leadin(self, pos):
        choice = self.leadin_at(pos)
        self.leadin_choice = choice
        scene = self.mapToScene(pos)
        if choice is None:
            self.draft.setPath(QPainterPath())
            self.label.set_text("Point closer to the figure", scene)
            return
        placement = self.leadin_part.placement
        shape = shape_of(placement.program)
        path = QPainterPath()
        dot = 4 / abs(self.transform().m11())
        for start, point in shape.with_leadin_at(choice).leadin_segments():
            a, b = placement.map_point(start), placement.map_point(point)
            path.moveTo(QPointF(*a))
            path.lineTo(QPointF(*b))
            path.addEllipse(QPointF(*b), dot, dot)
        self.draft.setPath(path)
        self.label.set_text(f"Lead-in: {shape.describe_leadin(choice)}", scene)

    def finish_leadin(self):
        part, choice = self.leadin_part, self.leadin_choice
        if choice is None:
            return
        self.stop_shape()
        self.window_.set_leadin_position(part, choice)

    def shape_press_event(self, event):
        if event.button() != Qt.LeftButton:
            self.stop_shape()
            return
        if self.shape_mode == "leadin":
            self.update_leadin(event.pos())
            self.finish_leadin()
            return
        if self.shape_start is None:
            self.shape_start = self.snap(event.pos())
            self.shape_press = event.pos()
            self.update_shape(event.pos())
        else:
            self.finish_shape(event.pos())

    def shape_release_event(self, event):
        # A drag ends at the release; a click leaves the first point set for a second click.
        if self.shape_mode in ("rectangle", "circle", "hole circle", "slot") and self.shape_start is not None \
                and self.shape_press is not None:
            moved = (event.pos() - self.shape_press).manhattanLength()
            self.shape_press = None
            if moved > QApplication.startDragDistance():
                self.finish_shape(event.pos())

    # ------------------------------------------------------------------- rotation

    def part_at(self, pos):
        for item in self.items(pos):
            while item is not None:
                if isinstance(item, PartItem):
                    return item
                item = item.parentItem()
        return None

    def pointer_angle(self, pos, pivot):
        """Angle from pivot to a view position, or None while too close to be reliable."""
        center = self.mapFromScene(QPointF(*pivot))
        if math.hypot(pos.x() - center.x(), pos.y() - center.y()) < ROTATE_MIN_RADIUS:
            return None
        position = self.mapToScene(pos)
        return math.degrees(math.atan2(position.y() - pivot[1], position.x() - pivot[0]))

    def begin_rotation(self, part, pos):
        if not part.isSelected():
            self.scene().clearSelection()
            part.setSelected(True)
        window = self.window_
        selected = window.selected_parts()
        pivot = window.selection_pivot(selected)
        self.rotation_drag = {
            "pivot": pivot,
            "originals": [(p, p.placement.x, p.placement.y, p.placement.angle) for p in selected],
            "grab_angle": self.pointer_angle(pos, pivot),
        }
        self.setCursor(Qt.CrossCursor)

    def continue_rotation(self, event):
        drag = self.rotation_drag
        pointer = self.pointer_angle(event.pos(), drag["pivot"])
        if pointer is None:
            return
        if drag["grab_angle"] is None:
            drag["grab_angle"] = pointer
        degrees = pointer - drag["grab_angle"]
        if event.modifiers() & Qt.ControlModifier:
            # One figure snaps to whole steps of its angle, a group to whole steps of the turn.
            base = drag["originals"][0][3] if len(drag["originals"]) == 1 else 0.0
            degrees = round((base + degrees) / ROTATE_SNAP) * ROTATE_SNAP - base
        self.window_.rotate_parts(drag["originals"], drag["pivot"], degrees)
        self.window_.schedule_refresh()
        if len(drag["originals"]) == 1:
            text = f"Angle {drag['originals'][0][0].placement.angle:.1f}°"
        else:
            text = f"Rotating {degrees:+.1f}°"
        self.window_.statusBar().showMessage(text)

    # ------------------------------------------------------------------- resizing

    def grip_at(self, pos):
        """(part, shape, handle, normal on the sheet) for a drawn figure's outline under the pointer."""
        scene = self.mapToScene(pos)
        reach = RESIZE_REACH / abs(self.transform().m11())
        for part in sorted(self.window_.parts, key=lambda p: not p.isSelected()):
            shape = part.drawn_shape
            if shape is None:
                continue
            found = shape.grip(part.placement.unmap_point((scene.x(), scene.y())), reach)
            if found is not None:
                normal = part.placement.map_vector(found[1]) if found[1] is not None else None
                return part, shape, found[0], normal
        return None

    @staticmethod
    def resize_cursor(normal):
        if normal is None:
            return Qt.CrossCursor  # a polygon's point, moved any way
        angle = math.degrees(math.atan2(normal[1], normal[0])) % 180.0
        if angle < 22.5 or angle >= 157.5:
            return Qt.SizeHorCursor
        if angle < 67.5:
            return Qt.SizeBDiagCursor  # up to the right on the screen, as Y points up
        if angle < 112.5:
            return Qt.SizeVerCursor
        return Qt.SizeFDiagCursor

    def update_grip_cursor(self, pos):
        found = self.grip_at(pos)
        if found is not None:
            self.viewport().setCursor(self.resize_cursor(found[3]))
            self.grip_cursor = True
        elif self.grip_cursor:
            self.viewport().unsetCursor()
            self.grip_cursor = False

    def begin_resize(self, found):
        part, shape, handle, _ = found
        self.scene().clearSelection()
        part.setSelected(True)
        self.resize_drag = {"part": part, "shape": shape, "handle": handle, "result": None}

    def continue_resize(self, pos):
        drag = self.resize_drag
        placement = drag["part"].placement
        scene = self.mapToScene(pos)
        shape = drag["shape"].resized(drag["handle"], placement.unmap_point((scene.x(), scene.y())))
        path = QPainterPath()
        try:
            program = shape.program()
        except ValueError as error:
            drag["result"] = None
            self.draft.setPath(path)
            self.label.set_text(str(error), scene)
            return
        drag["result"] = shape
        for cut in program.paths:
            if cut.kind == "cut":
                path.addPolygon(QPolygonF([QPointF(*placement.map_point(p)) for p in cut.points]))
        self.draft.setPath(path)
        if drag["handle"][0] == "point":
            x, y = placement.map_point(shape.points[drag["handle"][1]])
            self.label.set_text(f"X {format_mm(x)}   Y {format_mm(y)}", scene)
        else:
            self.label.set_text(shape.size_text(), scene)

    def end_resize(self, apply=True):
        drag, self.resize_drag = self.resize_drag, None
        self.draft.setPath(QPainterPath())
        self.label.set_text("", QPointF())
        if apply and drag["result"] is not None and drag["result"] != drag["shape"]:
            self.window_.replace_rectangle(drag["part"], drag["result"])

    def fit(self):
        rect = self.window_.sheet_item.boundingRect()
        for part in self.window_.parts:
            rect = rect.united(part.sceneBoundingRect())
        margin = max(rect.width(), rect.height()) * 0.04
        self.fitInView(rect.adjusted(-margin, -margin, margin, margin), Qt.KeepAspectRatio)
        self.window_.update_handles()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if event.modifiers() & Qt.ShiftModifier:
            step = self.window_.rotate_step.value()
            self.window_.rotate_selection(step if steps > 0 else -step)
            return
        factor = 1.2 ** steps
        current = abs(self.transform().m11())
        if 0.005 < current * factor < 200:
            # Keep the point under the pointer still. Qt's AnchorUnderMouse uses the screen position,
            # which is wrong when PlasmaNest is embedded in QtPlasmaC.
            anchor = event.position().toPoint() if hasattr(event, "position") else event.pos()
            before = self.mapToScene(anchor)
            self.setTransformationAnchor(QGraphicsView.NoAnchor)
            self.scale(factor, factor)
            after = self.mapToScene(anchor)
            self.translate(after.x() - before.x(), after.y() - before.y())
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self.window_.user_has_view = True  # keep this view when figures change
            self.window_.update_handles()

    MOUSE_EVENTS = (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseMove,
                    QEvent.MouseButtonDblClick)

    def viewportEvent(self, event):
        """Give mouse events a screen position that agrees with the view's own mapping.

        The scene finds the figure under a click by mapping the screen position back to the view.
        An embedded window does not know where it is on the screen, so that misses the figure.
        """
        if event.type() == QEvent.ToolTip:
            # Same problem for tooltips: find the item from the view's own position, and show the
            # tip at the pointer's real screen position, which comes from the X server.
            item = self.itemAt(event.pos())
            while item is not None and not item.toolTip():
                item = item.parentItem()
            if item is not None:
                QToolTip.showText(event.globalPos(), item.toolTip(), self.viewport())
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        if event.type() in self.MOUSE_EVENTS:
            local = event.localPos()
            screen = QPointF(self.viewport().mapToGlobal(QPoint(0, 0))) + local
            if screen != event.screenPos():
                fixed = QMouseEvent(event.type(), local, event.windowPos(), screen, event.button(),
                                    event.buttons(), event.modifiers())
                result = super().viewportEvent(fixed)
                event.setAccepted(fixed.isAccepted())
                return result
        return super().viewportEvent(event)

    def mousePressEvent(self, event):
        if self.shape_mode is not None and event.button() != Qt.MiddleButton:
            self.shape_press_event(event)
            event.accept()
            return
        if self.drawing is not None and event.button() == Qt.LeftButton:
            if self.near_first_corner(event.pos()):
                self.finish_drawing()
            else:
                point = self.draft_point(event.pos(), event.modifiers())
                if not self.drawing or point != self.drawing[-1]:
                    self.drawing.append(point)
                self.update_draft(None)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.drawing is None:
            found = self.grip_at(event.pos())
            if found is not None:
                self.begin_resize(found)
                event.accept()
                return
        if event.button() == Qt.RightButton and self.drawing is None:
            part = self.part_at(event.pos())
            if part is not None:
                self.begin_rotation(part, event.pos())
                event.accept()
                return
        if event.button() in (Qt.MiddleButton, Qt.RightButton):
            self.pan_from = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.resize_drag is not None:
            self.continue_resize(event.pos())
            event.accept()
            return
        if self.rotation_drag is not None:
            self.continue_rotation(event)
            event.accept()
            return
        if self.pan_from is not None:
            delta = event.pos() - self.pan_from
            self.pan_from = event.pos()
            scale = abs(self.transform().m11())
            self.setTransformationAnchor(QGraphicsView.NoAnchor)
            self.translate(delta.x() / scale, -delta.y() / scale)
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self.window_.user_has_view = True
            event.accept()
            return
        position = self.mapToScene(event.pos())
        if self.shape_mode is not None:
            self.update_shape(event.pos())
            event.accept()
            return
        self.window_.statusBar().showMessage(f"X {position.x():.1f}   Y {position.y():.1f} mm")
        if self.drawing is not None:
            self.update_draft(self.draft_point(event.pos(), event.modifiers()))
            event.accept()
            return
        super().mouseMoveEvent(event)
        if event.buttons() == Qt.NoButton:
            self.update_grip_cursor(event.pos())  # after Qt has set the cursor of the figure under it

    def mouseDoubleClickEvent(self, event):
        if self.drawing is not None and event.button() == Qt.LeftButton:
            # The first click of the double click has already placed the last corner.
            self.finish_drawing(closed=self.drawing_target == "sheet")  # a polygon figure: open
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
        if self.resize_drag is not None:
            if event.button() == Qt.LeftButton:
                self.end_resize()
            event.accept()
            return
        if self.rotation_drag is not None:
            self.rotation_drag = None
            self.unsetCursor()
            self.window_.refresh()
            self.window_.commit()
            event.accept()
            return
        if self.pan_from is not None:
            self.pan_from = None
            self.unsetCursor()
            event.accept()
            return
        if self.shape_mode is not None:
            self.shape_release_event(event)
            event.accept()
            return
        if self.drawing is not None:
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self.window_.commit()

    def keyPressEvent(self, event):
        if self.resize_drag is not None and event.key() == Qt.Key_Escape:
            self.end_resize(apply=False)
            return
        if self.shape_mode is not None and event.key() == Qt.Key_Escape:
            self.stop_shape()
            return
        if self.drawing is not None and self.drawing_key(event):
            return
        step = NUDGE_STEP_LARGE if event.modifiers() & Qt.ShiftModifier else NUDGE_STEP
        moves = {Qt.Key_Left: (-step, 0), Qt.Key_Right: (step, 0), Qt.Key_Up: (0, step), Qt.Key_Down: (0, -step)}
        if event.key() in moves:
            self.window_.nudge_selection(*moves[event.key()])
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.window_.user_has_view:
            self.fit()

    def showEvent(self, event):
        # Qt centres the view on showing it, on the last pointer position with AnchorUnderMouse:
        # the origin, before the pointer has moved. Embedded, PlasmaNest is shown after it has
        # been fitted, which left the sheet's corner in the middle of the view.
        super().showEvent(event)
        if not self.window_.user_has_view:
            self.fit()


class CopyDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Copy figure")
        self.count = QSpinBox()
        self.count.setRange(1, 500)
        self.count.setValue(1)
        form = QFormLayout(self)
        form.addRow("Number of copies:", self.count)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class AutonestDialog(QDialog):
    """The settings for packing figures against the sheet's bottom edge."""

    def __init__(self, parent, settings, selected):
        super().__init__(parent)
        self.setWindowTitle("Autonest")
        form = QFormLayout(self)
        which = "the selected figures" if selected else "all figures to cut"
        text = QLabel(f"Packs {which} against the bottom edge of the sheet, largest first. A figure "
                      "lying inside another, like a hole drawn in a part, moves with it. Figures that "
                      "do not fit are put beside the sheet.")
        text.setWordWrap(True)
        form.addRow(text)
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0, 1000)
        self.gap.setDecimals(1)
        self.gap.setSuffix(" mm")
        self.gap.setValue(settings["gap"])
        form.addRow("Gap between figures:", self.gap)
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0, 1000)
        self.margin.setDecimals(1)
        self.margin.setSuffix(" mm")
        self.margin.setValue(settings["margin"])
        form.addRow("Margin to the sheet edge:", self.margin)
        self.turn = QCheckBox("Turn figures 90°, 180° or 270° when it packs them lower")
        self.turn.setChecked(settings["turn"])
        form.addRow(self.turn)
        self.mirror = QCheckBox("Also try loaded G-code mirrored")
        self.mirror.setToolTip("Each loaded figure is also tried mirrored, at every angle, and kept mirrored "
                               "if that packs it lower. A mirrored figure is cut the other way round. "
                               "Figures drawn in PlasmaNest are never mirrored.")
        self.mirror.setChecked(settings.get("mirror", False))
        form.addRow(self.mirror)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def settings(self):
        return {"gap": self.gap.value(), "margin": self.margin.value(), "turn": self.turn.isChecked(),
                "mirror": self.mirror.isChecked()}


class CornerDialog(QDialog):
    """Type the corners of the sheet in order around its edge."""

    def __init__(self, parent, corners):
        super().__init__(parent)
        self.setWindowTitle("Sheet corners")
        self.sheet = None
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["X (mm)", "Y (mm)"])
        self.table.horizontalHeader().setStretchLastSection(True)
        for corner in corners:
            self.add_row(self.table.rowCount(), corner)
        add_button = QPushButton("Add corner")
        add_button.clicked.connect(lambda checked=False: self.add_after_selected())
        remove_button = QPushButton("Remove corner")
        remove_button.clicked.connect(lambda checked=False: self.remove_selected())
        row = QHBoxLayout()
        row.addWidget(add_button)
        row.addWidget(remove_button)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {COLOR_OUTSIDE.name()}; font-weight: bold;")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Enter the corners in order around the sheet."))
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.resize(320, 420)

    def add_row(self, row, corner):
        self.table.insertRow(row)
        for column, value in enumerate(corner):
            self.table.setItem(row, column, QTableWidgetItem(f"{value:g}"))

    def add_after_selected(self):
        current = self.table.currentRow()
        row = current + 1 if current >= 0 else self.table.rowCount()
        self.add_row(row, (0.0, 0.0))
        self.table.setCurrentCell(row, 0)

    def remove_selected(self):
        if self.table.currentRow() >= 0:
            self.table.removeRow(self.table.currentRow())

    def corners(self):
        result = []
        for row in range(self.table.rowCount()):
            values = []
            for column in range(2):
                item = self.table.item(row, column)
                text = item.text().strip().replace(",", ".") if item else ""
                try:
                    value = float(text)
                except ValueError:
                    raise ValueError(f"Row {row + 1}: \"{text}\" is not a number.")
                if not math.isfinite(value):
                    raise ValueError(f"Row {row + 1}: \"{text}\" is not a number.")
                values.append(value)
            result.append(tuple(values))
        return result

    def accept(self):
        try:
            self.sheet = Sheet(self.corners())
        except ValueError as error:
            self.error.setText(str(error))
            return
        super().accept()


class PointsPreview(QWidget):
    """Drawing of the measured corners, the edges between them and the sheet after the margin."""

    pointClicked = pyqtSignal(int)
    PADDING = 28      # pixels around the drawing
    MIN_SPAN = 100.0  # mm shown at least, so one or two points do not fill the view
    POINT_RADIUS = 5
    CLICK_RADIUS = 12

    def __init__(self):
        super().__init__()
        self.corners = []
        self.sheet = None
        self.selected = -1
        self.setMinimumSize(320, 320)

    def sizeHint(self):
        return QSize(460, 460)

    def set_data(self, corners, sheet, selected):
        self.corners, self.sheet, self.selected = list(corners), sheet, selected
        self.update()

    def transform(self):
        """Scale and offset from mm to pixels, Y up, same scale on both axes."""
        xs = [c[0] for c in self.corners]
        ys = [c[1] for c in self.corners]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        span_x, span_y = max(x1 - x0, self.MIN_SPAN), max(y1 - y0, self.MIN_SPAN)
        width, height = self.width() - 2 * self.PADDING, self.height() - 2 * self.PADDING
        scale = min(width / span_x, height / span_y)
        return scale, self.width() / 2 - cx * scale, self.height() / 2 + cy * scale

    def to_pixels(self, point, transform):
        scale, ox, oy = transform
        return QPointF(ox + point[0] * scale, oy - point[1] * scale)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), COLOR_VIEW)
        painter.setPen(QPen(COLOR_GRID))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        if not self.corners:
            painter.setPen(QPen(COLOR_VIEW))
            painter.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap,
                             "No points yet.\nSave the first corner in QtPlasmaC.")
            return
        transform = self.transform()
        pixels = [self.to_pixels(c, transform) for c in self.corners]

        origin = self.to_pixels((0.0, 0.0), transform)
        if self.rect().contains(origin.toPoint()):
            painter.setPen(QPen(COLOR_ORIGIN, 1.5))
            painter.drawLine(origin + QPointF(-8, 0), origin + QPointF(8, 0))
            painter.drawLine(origin + QPointF(0, -8), origin + QPointF(0, 8))
            painter.drawText(origin + QPointF(5, 14), "X0 Y0")

        if self.sheet is not None:
            polygon = QPolygonF([self.to_pixels(c, transform) for c in self.sheet.corners])
            painter.setPen(QPen(COLOR_SELECTED, 1.5, Qt.DashLine))
            painter.setBrush(QBrush(QColor(COLOR_SELECTED.red(), COLOR_SELECTED.green(), COLOR_SELECTED.blue(), 40)))
            painter.drawPolygon(polygon)
            painter.setBrush(Qt.NoBrush)

        edge = COLOR_SHEET_EDGE if self.sheet is not None or len(pixels) < 3 else COLOR_OUTSIDE
        painter.setPen(QPen(edge, 2))
        for a, b in zip(pixels, pixels[1:]):
            painter.drawLine(a, b)
        if len(pixels) >= 3:
            painter.setPen(QPen(edge, 1.5, Qt.DashLine))
            painter.drawLine(pixels[-1], pixels[0])

        for number, point in enumerate(pixels):
            newest = number == len(pixels) - 1
            color = COLOR_DRAFT if newest else COLOR_CUT
            radius = self.POINT_RADIUS + (2 if newest else 0)
            if number == self.selected:
                painter.setPen(QPen(COLOR_SELECTED, 3))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(point, radius + 5, radius + 5)
            painter.setPen(QPen(color, 1))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(point, radius, radius)
            painter.setPen(QPen(color))
            label = point + QPointF(radius + 3, -radius - 3)
            painter.drawText(label, str(number + 1))

    def mousePressEvent(self, event):
        if not self.corners:
            return
        transform = self.transform()
        pos = QPointF(event.pos())
        distances = [(math.hypot(p.x() - pos.x(), p.y() - pos.y()), i)
                     for i, p in enumerate(self.to_pixels(c, transform) for c in self.corners)]
        distance, index = min(distances)
        if distance <= self.CLICK_RADIUS:
            self.pointClicked.emit(index)


class MachineDialog(QDialog):
    """Sheet corners saved from QtPlasmaC with the save point user button, live as they arrive."""

    POLL_MS = 500

    def __init__(self, parent, path=machine.POINTS_FILE, offsets=machine.current_offsets):
        super().__init__(parent)
        self.setWindowTitle("Sheet from the machine")
        self.path = path
        self.read_offsets = offsets
        self.points = []
        self.offsets = None
        self.file_state = False  # never equal to a real state, so the first poll reads the file
        self.error = None
        self.sheet = None

        intro = QLabel("Jog to the corners of the sheet in order around it and press the <b>save point</b> user "
                       "button in QtPlasmaC at each one. With the laser on, the point under the laser is saved.")
        intro.setWordWrap(True)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["X (mm)", "Y (mm)", "Pointer", "Saved"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.coordinates = QLabel()
        self.coordinates.setWordWrap(True)
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0, 500)
        self.margin.setDecimals(1)
        self.margin.setValue(5.0)
        self.margin.setSuffix(" mm")
        self.margin.valueChanged.connect(lambda value: self.update_result())
        margin_row = QHBoxLayout()
        margin_row.addWidget(QLabel("Safety margin from the edge:"))
        margin_row.addWidget(self.margin)
        remove_button = QPushButton("Remove point")
        remove_button.clicked.connect(lambda checked=False: self.remove_selected())
        clear_button = QPushButton("Clear all")
        clear_button.clicked.connect(lambda checked=False: self.clear())
        row = QHBoxLayout()
        row.addWidget(remove_button)
        row.addWidget(clear_button)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Use as sheet")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.preview = PointsPreview()
        self.preview.pointClicked.connect(lambda index: self.table.setCurrentCell(index, 0))
        self.table.currentCellChanged.connect(lambda *args: self.update_preview())
        legend = QLabel(f"<span style='color:{COLOR_DRAFT.name()}'>●</span> latest point &nbsp; "
                        f"<span style='color:{COLOR_SELECTED.name()}'>▨</span> sheet after the margin")
        controls = QVBoxLayout()
        controls.addWidget(intro)
        controls.addWidget(self.table)
        controls.addLayout(row)
        controls.addWidget(self.coordinates)
        controls.addLayout(margin_row)
        controls.addWidget(self.result)
        drawing = QVBoxLayout()
        drawing.addWidget(self.preview, 1)
        drawing.addWidget(legend)
        columns = QHBoxLayout()
        columns.addLayout(controls, 0)
        columns.addLayout(drawing, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(columns, 1)
        layout.addWidget(self.buttons)
        self.resize(960, 560)

        self.timer = QTimer(self)
        self.timer.setInterval(self.POLL_MS)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.poll()

    def poll(self):
        try:
            info = os.stat(self.path)
            file_state = (info.st_mtime_ns, info.st_size)
        except OSError:
            file_state = None
        offsets = self.read_offsets()
        if file_state == self.file_state and offsets == self.offsets:
            return
        self.file_state, self.offsets = file_state, offsets
        try:
            self.points = machine.read_points(self.path)
            self.error = None
        except (OSError, machine.PointsError) as error:
            self.points = []
            self.error = str(error)
        self.show_points()

    def corners(self):
        return machine.work_corners(self.points, self.offsets)

    def show_points(self):
        selected = self.table.currentRow()
        self.table.setRowCount(0)
        for row, (point, corner) in enumerate(zip(self.points, self.corners())):
            self.table.insertRow(row)
            values = [machine.mm(corner[0]), machine.mm(corner[1]), machine.SOURCE_NAMES[point.source],
                      point.time.split("T")[-1]]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        if 0 <= selected < self.table.rowCount():
            self.table.setCurrentCell(selected, 0)
        if self.offsets is None:
            self.coordinates.setText("LinuxCNC is not answering. The points are shown with the origin that applied when "
                                     "they were saved.")
        else:
            o = self.offsets
            text = f"Work coordinates in {o.name} with the current origin (X {o.g5x[0]:.2f} Y {o.g5x[1]:.2f}"
            if o.rotation:
                text += f", rotation {o.rotation:.3f}°"
            if any(o.g92):
                text += f", G92 X {o.g92[0]:.2f} Y {o.g92[1]:.2f}"
            self.coordinates.setText(text + ").")
        self.update_result()

    def update_result(self):
        self.sheet = None
        if self.error:
            message = self.error
        elif len(self.points) < 3:
            message = f"{plural(len(self.points), 'point')} saved. At least three are needed."
        else:
            try:
                self.sheet = Sheet(self.corners()).inset(self.margin.value())
                message = None
            except ValueError as error:
                message = str(error)
        if message:
            self.result.setStyleSheet(f"color: {COLOR_OUTSIDE.name()}; font-weight: bold;")
            self.result.setText(message)
        else:
            s = self.sheet
            self.result.setStyleSheet("")
            self.result.setText(f"Sheet with {len(s.corners)} corners, {s.width:.1f} × {s.height:.1f} mm, "
                                f"{s.area / 1e6:.2f} m² after the margin.")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(self.sheet is not None)
        self.update_preview()

    def update_preview(self):
        self.preview.set_data(self.corners() if not self.error else [], self.sheet, self.table.currentRow())

    def rewrite(self, points):
        try:
            machine.write_points(points, self.path)
        except OSError as error:
            QMessageBox.warning(self, APP_NAME, f"Could not write the points file:\n{error}")
        self.file_state = False  # read it back even if the size and time look the same
        self.poll()

    def remove_selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.points):
            self.rewrite(self.points[:row] + self.points[row + 1:])

    def clear(self):
        self.rewrite([])

    def accept(self):
        self.poll()
        if self.sheet is not None:
            super().accept()


class CompactList(QListWidget):
    """Asks for little height, and takes what is free, so the panel only scrolls when it must."""

    def sizeHint(self):
        return QSize(super().sizeHint().width(), 90)


class MainWindow(QMainWindow):
    def __init__(self):
        # Number fields with a decimal point and no thousands separator, whatever the system's
        # language. It applies to widgets made from here on.
        QLocale.setDefault(QLocale.c())
        super().__init__()
        self.parts = []
        self.project_path = None
        self.user_has_view = False
        self.undo_stack = []
        self.undo_index = -1
        self.saved_state = None
        self.updating_ui = False

        self.scene_ = QGraphicsScene(self)
        self.scene_.setSceneRect(-100000, -100000, 200000, 200000)
        # A new sheet is as large as QtPlasmaC's work area, or 3000 x 1500 mm without LinuxCNC.
        self.sheet_item = SheetItem(Sheet.rectangle(*(machine_work_area() or DEFAULT_SHEET)))
        self.scene_.addItem(self.sheet_item)
        self.scene_.addItem(OriginItem())
        self.travel = QGraphicsPathItem()
        self.travel.setPen(cosmetic_pen(COLOR_RAPID, 1.0, Qt.DotLine))
        self.travel.setAcceptedMouseButtons(Qt.NoButton)
        self.travel.setZValue(-1)
        self.scene_.addItem(self.travel)
        self.view = NestView(self.scene_, self)
        self.setCentralWidget(self.view)
        self.outside_count = self.overlap_count = 0

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(60)
        self.refresh_timer.timeout.connect(self.refresh)

        self.materials = available_materials()
        self.rectangle_defaults = {"inside": False, "material": None, "kerf": 0.0, "leadin": 0.0}
        self.hole_circle_defaults = {"holes": 4, "hole_diameter": 10.0}
        self.autonest_settings = {"gap": PART_GAP, "margin": PART_GAP, "turn": True, "mirror": False}
        self._build_panel()
        self._build_actions()
        self.scene_.selectionChanged.connect(self.selection_changed)
        self.statusBar().showMessage("Load a G-code file to begin.")
        self.resize(1280, 800)
        self.commit()
        self.saved_state = self.undo_stack[-1]
        self.update_title()
        self.selection_changed()

    # ------------------------------------------------------------------ set-up

    def _build_panel(self):
        panel = QWidget()
        panel.setMaximumWidth(340)
        layout = QVBoxLayout(panel)

        sheet_box = QGroupBox("Steel sheet (mm)")
        sheet_form = QFormLayout(sheet_box)
        self.sheet_width = self._length_spin(1, 100000, self.sheet.width)
        self.sheet_height = self._length_spin(1, 100000, self.sheet.height)
        self.sheet_width.editingFinished.connect(self.sheet_changed)
        self.sheet_height.editingFinished.connect(self.sheet_changed)
        sheet_form.addRow("Width (X):", self.sheet_width)
        sheet_form.addRow("Height (Y):", self.sheet_height)
        row = QHBoxLayout()
        self.draw_button = self._button("Draw", self.toggle_drawing, row)
        self.corners_button = self._button("Corners…", self.edit_corners, row)
        self.rectangle_button = self._button("Rectangle", self.make_rectangle, row)
        sheet_form.addRow(row)
        row = QHBoxLayout()
        self.machine_button = self._button("From the machine…", self.sheet_from_machine, row)
        sheet_form.addRow(row)
        self.sheet_info = QLabel()
        self.sheet_info.setWordWrap(True)
        sheet_form.addRow(self.sheet_info)
        layout.addWidget(sheet_box)
        self.update_sheet_panel()

        parts_box = QGroupBox("Figures (in cutting order)")
        parts_layout = QVBoxLayout(parts_box)
        self.part_list = CompactList()
        self.part_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.part_list.itemSelectionChanged.connect(self.list_selection_changed)
        self.part_list.itemChanged.connect(self.list_item_changed)
        parts_layout.addWidget(self.part_list)
        row = QHBoxLayout()
        self.load_button = self._button("Load…", self.load_files, row)
        self.copy_button = self._button("Copy…", self.copy_selection, row)
        self.autonest_button = self._button("Autonest…", self.autonest_dialog, row)
        self.autonest_button.setToolTip("Pack the selected figures, or all figures to cut, against the "
                                        "bottom edge of the sheet.")
        parts_layout.addLayout(row)
        row = QHBoxLayout()
        self.rectangle_shape_button = self._button("Draw rectangle", self.toggle_rectangle, row)
        self.rectangle_shape_button.setToolTip("Press at a corner and drag to the opposite corner.")
        self.circle_shape_button = self._button("Draw circle", self.toggle_circle, row)
        self.circle_shape_button.setToolTip("Press at the centre and drag out to the edge.")
        parts_layout.addLayout(row)
        row = QHBoxLayout()
        self.polygon_shape_button = self._button("Draw polygon", self.toggle_polygon, row)
        self.polygon_shape_button.setToolTip("Click the points. A click on the first point closes it; a double "
                                             "click or Enter leaves it open.")
        parts_layout.addLayout(row)
        row = QHBoxLayout()
        self.hole_circle_shape_button = self._button("Draw hole circle", self.toggle_hole_circle, row)
        self.hole_circle_shape_button.setToolTip("Press at the centre and drag out to the hole centres.")
        self.slot_shape_button = self._button("Draw slot", self.toggle_slot, row)
        self.slot_shape_button.setToolTip("Press at a corner and drag to the opposite corner.")
        parts_layout.addLayout(row)
        row = QHBoxLayout()
        self.conversational_button = self._button("Get from CONVERSATIONAL", self.load_conversational, row)
        self.conversational_button.setToolTip("Adds the shapes accepted in QtPlasmaC's CONVERSATIONAL.")
        self.conversational_button.setVisible(os.path.isdir(os.path.dirname(CONVERSATIONAL_FILE)))
        parts_layout.addLayout(row)
        row = QHBoxLayout()
        self.delete_button = self._button("Delete", self.delete_selection, row)
        self.delete_all_button = self._button("Delete the whole G-code", self.delete_programs, row)
        parts_layout.addLayout(row)
        row = QHBoxLayout()
        self.up_button = self._button("Cut earlier", lambda: self.reorder(-1), row)
        self.down_button = self._button("Cut later", lambda: self.reorder(1), row)
        parts_layout.addLayout(row)
        layout.addWidget(parts_box, 1)

        place_box = QGroupBox("Placement")
        place_form = QFormLayout(place_box)
        self.pos_x = self._length_spin(-100000, 100000, 0)
        self.pos_y = self._length_spin(-100000, 100000, 0)
        self.angle = QDoubleSpinBox()
        self.angle.setRange(-360, 720)
        self.angle.setDecimals(2)
        self.angle.setSuffix(" °")
        self.angle.setKeyboardTracking(False)
        self.pos_x.valueChanged.connect(self.placement_edited)
        self.pos_y.valueChanged.connect(self.placement_edited)
        self.angle.valueChanged.connect(self.placement_edited)
        self.pos_x_label, self.pos_y_label = QLabel("X (left edge):"), QLabel("Y (bottom edge):")
        place_form.addRow(self.pos_x_label, self.pos_x)
        place_form.addRow(self.pos_y_label, self.pos_y)
        place_form.addRow("Angle:", self.angle)
        row = QHBoxLayout()
        self.rotate_step = QDoubleSpinBox()
        self.rotate_step.setRange(0.01, 180)
        self.rotate_step.setValue(90)
        self.rotate_step.setSuffix(" °")
        self.rotate_ccw = self._button("↺", lambda: self.rotate_selection(self.rotate_step.value()), row)
        self.rotate_cw = self._button("↻", lambda: self.rotate_selection(-self.rotate_step.value()), row)
        row.addWidget(self.rotate_step)
        place_form.addRow("Rotate:", row)
        row = QHBoxLayout()
        self.mirror_x_button = self._button("Left–right", lambda: self.mirror_selection(upside_down=False), row)
        self.mirror_x_button.setToolTip("Mirror the selection left to right.")
        self.mirror_y_button = self._button("Upside down", lambda: self.mirror_selection(upside_down=True), row)
        self.mirror_y_button.setToolTip("Turn the selection upside down: mirrored top to bottom.")
        place_form.addRow("Mirror:", row)
        self.place_box = place_box
        layout.addWidget(place_box)

        # Only for one selected rectangle drawn in PlasmaNest.
        rect_box = QGroupBox("Rectangle")
        rect_form = QFormLayout(rect_box)
        self.size_w = self._length_spin(1, 100000, 100)
        self.size_h = self._length_spin(1, 100000, 100)
        self.size_w_label, self.size_h_label = QLabel("Width:"), QLabel("Height:")
        rect_form.addRow(self.size_w_label, self.size_w)
        rect_form.addRow(self.size_h_label, self.size_h)
        self.size_d = self._length_spin(1, 100000, 100)
        self.size_d_label = QLabel("Diameter:")
        rect_form.addRow(self.size_d_label, self.size_d)
        self.hole_count = QSpinBox()
        self.hole_count.setRange(2, 360)
        self.hole_count.setKeyboardTracking(False)
        self.hole_count_label = QLabel("Holes:")
        rect_form.addRow(self.hole_count_label, self.hole_count)
        self.hole_d = self._length_spin(1, 100000, 10)
        self.hole_d_label = QLabel("Hole diameter:")
        rect_form.addRow(self.hole_d_label, self.hole_d)
        self.hole_start = QDoubleSpinBox()
        self.hole_start.setRange(-360, 360)
        self.hole_start.setDecimals(2)
        self.hole_start.setSuffix(" °")
        self.hole_start_label = QLabel("First hole at:")
        self.hole_start_label.setToolTip("Angle from +X to the first hole. The rest follow anticlockwise.")
        rect_form.addRow(self.hole_start_label, self.hole_start)
        self.poly_closed = QCheckBox("Closed")
        self.poly_closed.setToolTip("Closed: a part or a hole. Open: a line cut from the first point to the "
                                    "last, with no kerf offset and no lead-in.")
        rect_form.addRow(self.poly_closed)
        self.poly_table = QTableWidget(0, 2)
        self.poly_table.setHorizontalHeaderLabels(["X (mm)", "Y (mm)"])
        self.poly_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.poly_table.setMinimumHeight(120)
        self.poly_table.setMaximumHeight(200)
        self.poly_table.setToolTip("The points on the sheet. Type a new value, or drag a point in the view.")
        rect_form.addRow(self.poly_table)
        self.poly_buttons = QWidget()
        buttons = QHBoxLayout(self.poly_buttons)
        buttons.setContentsMargins(0, 0, 0, 0)
        self.poly_add = self._button("Add point", self.add_polygon_point, buttons)
        self.poly_add.setToolTip("A new point halfway along the edge after the chosen row.")
        self.poly_remove = self._button("Remove point", self.remove_polygon_point, buttons)
        rect_form.addRow(self.poly_buttons)
        self.rect_material = QComboBox()
        self.rect_material.addItem("Active in QtPlasmaC", None)
        for material in self.materials:
            self.rect_material.addItem(f"{material.number}: {material.name}", material.number)
        rect_form.addRow("Material:", self.rect_material)
        self.rect_side = QComboBox()
        self.rect_side.addItem("Outside (part)", False)
        self.rect_side.addItem("Inside (hole)", True)
        self.rect_side_label = QLabel("Cut:")
        rect_form.addRow(self.rect_side_label, self.rect_side)
        self.rect_kerf = self._length_spin(0, 20, 0)
        self.rect_kerf.setSuffix(" mm")
        rect_form.addRow("Kerf:", self.rect_kerf)
        self.rect_leadin = self._length_spin(0, 1000, 0)
        self.rect_leadin.setSuffix(" mm")
        rect_form.addRow("Lead-in:", self.rect_leadin)
        self.leadin_button = QPushButton("Place lead-in")
        self.leadin_button.setToolTip("Point at or near the figure and click where the lead-in should be.")
        self.leadin_button.clicked.connect(lambda checked=False: self.place_leadin())
        rect_form.addRow(self.leadin_button)
        self.rect_error = QLabel()
        self.rect_error.setWordWrap(True)
        self.rect_error.setStyleSheet(f"color: {COLOR_OUTSIDE.name()}; font-weight: bold;")
        self.rect_error.hide()
        rect_form.addRow(self.rect_error)
        for spin in (self.size_w, self.size_h, self.size_d, self.hole_d, self.hole_start, self.rect_kerf,
                     self.rect_leadin):
            spin.editingFinished.connect(self.rectangle_edited)
        self.hole_count.valueChanged.connect(lambda value: self.rectangle_edited())
        self.poly_closed.toggled.connect(lambda checked: self.rectangle_edited())
        self.poly_table.itemChanged.connect(self.polygon_point_edited)
        for combo in (self.rect_material, self.rect_side):
            combo.currentIndexChanged.connect(lambda index: self.rectangle_edited())
        self.rect_box = rect_box
        layout.addWidget(rect_box)
        self.show_rectangle_fields(None)

        self.show_rapids = QCheckBox("Show rapids (G0)")
        self.show_rapids.setChecked(True)
        self.show_rapids.toggled.connect(self.rapids_toggled)
        layout.addWidget(self.show_rapids)

        # Only shown when embedded in QtPlasmaC, see enable_live_export.
        self.autoload = QCheckBox("Load into QtPlasmaC when going to MAIN")
        self.autoload.setChecked(True)
        self.autoload.setToolTip("The nest is loaded into the preview when you go from PLASMANEST to MAIN.")
        self.autoload.toggled.connect(lambda checked: self.write_live_export())
        self.autoload.hide()
        layout.addWidget(self.autoload)
        self.allow_overlaps = QCheckBox("Allow overlapping figures")
        self.allow_overlaps.setToolTip("Overlapping figures are still shown in the warning colour, but they no "
                                       "longer stop the nest going to MAIN or ask before an export.")
        self.allow_overlaps.toggled.connect(self.allow_overlaps_toggled)
        layout.addWidget(self.allow_overlaps)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        help_label = QLabel("Left drag: move or select.\n"
                            "Right drag on a figure: rotate (Ctrl: 15° steps).\n"
                            "Wheel: zoom. Shift+wheel: rotate.\n"
                            "Right drag on empty space or middle drag: pan.\n"
                            "Arrows: move 1 mm (Shift: 10 mm).")
        help_label.setWordWrap(True)
        help_label.setStyleSheet(f"color: {COLOR_HELP.name()};")
        layout.addWidget(help_label)

        dock = QDockWidget("Tools", self)
        dock.setObjectName("panel")
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        # Machine screens can be small: the panel scrolls rather than pushing the window off screen.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        scroll.setFixedWidth(panel.maximumWidth() + scroll.verticalScrollBar().sizeHint().width())
        self.panel_scroll = scroll
        dock.setWidget(scroll)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _length_spin(self, low, high, value):
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(2)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin

    def _button(self, text, slot, layout):
        button = QPushButton(text)
        button.clicked.connect(lambda checked=False: slot())
        layout.addWidget(button)
        return button

    def _build_actions(self):
        file_menu = self.menuBar().addMenu("&File")
        edit_menu = self.menuBar().addMenu("&Edit")
        view_menu = self.menuBar().addMenu("&View")
        toolbar = self.addToolBar("Tools")
        toolbar.setObjectName("toolbar")

        def action(menu, text, slot, shortcut=None, tool=False):
            item = QAction(text, self)
            item.triggered.connect(lambda checked=False: slot())
            if shortcut:
                item.setShortcut(shortcut)
            menu.addAction(item)
            if tool:
                toolbar.addAction(item)
            return item

        action(file_menu, "&New project", self.new_project, QKeySequence.New)
        action(file_menu, "&Open project…", self.open_project, QKeySequence.Open, True)
        action(file_menu, "&Save project", self.save, QKeySequence.Save, True)
        action(file_menu, "Save project &as…", self.save_as, QKeySequence.SaveAs)
        file_menu.addSeparator()
        action(file_menu, "&Load G-code…", self.load_files, "Ctrl+L", True)
        self.export_action = action(file_menu, "&Export to QtPlasmaC (.ngc)…", self.export, "Ctrl+E", True)
        file_menu.addSeparator()
        action(file_menu, "&Quit", self.close, QKeySequence.Quit)

        self.undo_action = action(edit_menu, "&Undo", self.undo, QKeySequence.Undo)
        self.redo_action = action(edit_menu, "&Redo", self.redo, QKeySequence.Redo)
        edit_menu.addSeparator()
        action(edit_menu, "&Copy figure…", self.copy_selection, "Ctrl+D")
        action(edit_menu, "&Delete figure", self.delete_selection, QKeySequence.Delete)
        action(edit_menu, "Select &all", self.select_all, QKeySequence.SelectAll)
        action(edit_menu, "R&otate", lambda: self.rotate_selection(self.rotate_step.value()), "R")
        action(edit_menu, "&Mirror left–right", lambda: self.mirror_selection(upside_down=False))
        action(edit_menu, "Turn &upside down", lambda: self.mirror_selection(upside_down=True))
        action(edit_menu, "Auto&nest…", self.autonest_dialog)

        action(view_menu, "&Fit to window", self.fit_view, "F", True)

    # --------------------------------------------------------------- selection

    def selected_parts(self):
        return [p for p in self.parts if p.isSelected()]

    def select_all(self):
        for part in self.parts:
            part.setSelected(True)

    def selection_changed(self):
        if self.updating_ui:
            return
        self.updating_ui = True
        selected = self.selected_parts()
        for row, part in enumerate(self.parts):
            self.part_list.item(row).setSelected(part.isSelected())
        self.updating_ui = False
        has_selection = bool(selected)
        for widget in (self.copy_button, self.delete_button, self.delete_all_button,
                       self.up_button, self.down_button, self.rotate_ccw, self.rotate_cw,
                       self.mirror_x_button, self.mirror_y_button):
            widget.setEnabled(has_selection)
        self.place_box.setEnabled(has_selection)
        self.pos_x.setEnabled(len(selected) == 1)
        self.pos_y.setEnabled(len(selected) == 1)
        self.angle.setEnabled(len(selected) == 1)
        self.update_handles()
        self.update_placement_fields()

    def list_item_changed(self, item):
        """A check box in the list: whether the figure is cut."""
        if self.updating_ui:
            return
        part = self.parts[self.part_list.row(item)]
        cut = item.checkState() == Qt.Checked
        if part.placement.cut != cut:
            part.placement.cut = cut
            part.update()
            self.refresh()
            self.commit()

    def list_selection_changed(self):
        if self.updating_ui:
            return
        self.updating_ui = True
        for row, part in enumerate(self.parts):
            part.setSelected(self.part_list.item(row).isSelected())
        self.updating_ui = False
        self.selection_changed()

    def update_handles(self):
        selected = self.selected_parts()
        scale = abs(self.view.transform().m11()) or 1.0
        for part in self.parts:
            show = len(selected) == 1 and part.isSelected()
            part.handle.setVisible(show)
            if show:
                part.handle.setPos(0, part.rect.bottom() + HANDLE_DISTANCE / scale)

    def show_rectangle_fields(self, shape, part=None):
        """The box for a figure drawn in PlasmaNest: a rectangle or a circle.

        Hiding a field that has the keyboard focus makes Qt report it as edited, with the old
        figure's values still in the fields. Hence no edits while switching, and edits only apply
        to the part whose values the fields show.
        """
        updating = self.updating_ui
        self.updating_ui = True
        try:
            same_part = part is not None and part is getattr(self, "fields_part", None)
            self.fields_part = part if shape is not None else None
            self.rect_box.setVisible(shape is not None)
            if shape is None:
                return
            round_shape = isinstance(shape, (Circle, HoleCircle))
            hole_circle = isinstance(shape, HoleCircle)
            self.rect_box.setTitle({Circle: "Circle", HoleCircle: "Hole circle", Slot: "Slot",
                                    Polygon: "Polygon"}.get(type(shape), "Rectangle"))
            polygon = isinstance(shape, Polygon)
            slot = isinstance(shape, Slot)
            self.size_w_label.setText("Length:" if slot else "Width:")
            self.size_h_label.setText("Width:" if slot else "Height:")
            for widget in (self.size_w_label, self.size_w, self.size_h_label, self.size_h):
                widget.setVisible(not round_shape and not polygon)
            for widget in (self.poly_closed, self.poly_table, self.poly_buttons):
                widget.setVisible(polygon)
            for widget in (self.size_d_label, self.size_d):
                widget.setVisible(round_shape)
            self.size_d_label.setText("Circle diameter:" if hole_circle else "Diameter:")
            for widget in (self.hole_count_label, self.hole_count, self.hole_d_label, self.hole_d,
                           self.hole_start_label, self.hole_start):
                widget.setVisible(hole_circle)
            for widget in (self.rect_side_label, self.rect_side):
                widget.setVisible(not hole_circle)  # only holes
            self._fill_shape_fields(shape, keep_typing=same_part)
        finally:
            self.updating_ui = updating

    def _fill_shape_fields(self, shape, keep_typing=True):
        """keep_typing: leave a field with the keyboard focus alone, as it may be half typed. Not
        for another figure, which would otherwise get the old figure's value."""
        if isinstance(shape, HoleCircle):
            sizes = ((self.size_d, shape.diameter), (self.hole_count, shape.holes), (self.hole_d, shape.hole_diameter),
                     (self.hole_start, shape.start))
        elif isinstance(shape, Circle):
            sizes = ((self.size_d, shape.diameter),)
        elif isinstance(shape, Slot):
            sizes = ((self.size_w, shape.length), (self.size_h, shape.width))
        elif isinstance(shape, Polygon):
            sizes = ()
            self._fill_polygon_points(shape)
        else:
            sizes = ((self.size_w, shape.width), (self.size_h, shape.height))
        for spin, value in sizes + ((self.rect_kerf, shape.kerf), (self.rect_leadin, shape.leadin)):
            if not (keep_typing and spin.hasFocus()):
                spin.setValue(value)
        index = self.rect_material.findData(shape.material)
        if index < 0:  # a material that is not in this machine's file
            self.rect_material.addItem(f"{shape.material}: (not in the material file)", shape.material)
            index = self.rect_material.count() - 1
        self.rect_material.setCurrentIndex(index)
        self.rect_side.setCurrentIndex(1 if shape.inside else 0)
        self.rect_kerf.setEnabled(shape.material is None or self.material_kerf(shape.material) is None)
        small_hole = isinstance(shape, (Circle, HoleCircle)) and shape.small_hole
        line = isinstance(shape, Polygon) and not shape.closed  # no inside: no side or kerf
        if line:
            self.rect_kerf.setEnabled(False)
        self.rect_side.setEnabled(not line)
        self.rect_leadin.setEnabled(not small_hole)
        self.rect_leadin.setToolTip(
            f"Holes under {format_number(SMALL_HOLE)} mm start at the centre with a half circle, are cut at "
            f"{SMALL_HOLE_SPEED} % speed and end with a half circle, at most {format_number(OVERCUT)} mm."
            if small_hole else "")

    def _fill_polygon_points(self, shape):
        """The points as they lie on the sheet."""
        part = getattr(self, "fields_part", None)
        self.poly_closed.setChecked(shape.closed)
        table = self.poly_table
        table.blockSignals(True)
        try:
            table.setRowCount(len(shape.points))
            for row, point in enumerate(shape.points):
                x, y = part.placement.map_point(point) if part is not None else point
                for column, value in enumerate((x, y)):
                    table.setItem(row, column, QTableWidgetItem(f"{value:.2f}"))
        finally:
            table.blockSignals(False)

    def selected_polygon(self):
        part, shape = self.selected_rectangle()
        if not isinstance(shape, Polygon) or part is not getattr(self, "fields_part", None):
            return None, None
        return part, shape

    def polygon_point_edited(self, item):
        """A value typed in the points table: that point moves, the others stay exactly where they are."""
        if self.updating_ui:
            return
        part, shape = self.selected_polygon()
        if shape is None:
            return
        row = item.row()
        try:
            x, y = (float(self.poly_table.item(row, column).text().strip().replace(",", "."))
                    for column in (0, 1))
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError
        except (ValueError, AttributeError):
            self.rect_error.setText(f"Row {row + 1}: not a number.")
            self.rect_error.show()
            self.show_rectangle_fields(shape, part)
            return
        if self.replace_rectangle(part, shape.with_point(row, part.placement.unmap_point((x, y)))) is None:
            self.show_rectangle_fields(shape, part)

    def add_polygon_point(self):
        """Halfway along the edge after the chosen row, or the last edge; an open polygon's last
        row gets a point as far on again."""
        part, shape = self.selected_polygon()
        if shape is None:
            return
        points = list(shape.points)
        rows = self.poly_table.selectionModel().selectedRows() or []
        current = self.poly_table.currentRow()
        row = current if current >= 0 else (rows[0].row() if rows else len(points) - 1)
        if not shape.closed and row == len(points) - 1:
            a, b = points[-2], points[-1]
            new = (2 * b[0] - a[0], 2 * b[1] - a[1])
        else:
            a, b = points[row], points[(row + 1) % len(points)]
            new = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        points.insert(row + 1, (float(round(new[0])), float(round(new[1]))))
        self.replace_rectangle(part, replace(shape, points=tuple(points)))

    def remove_polygon_point(self):
        part, shape = self.selected_polygon()
        if shape is None:
            return
        row = self.poly_table.currentRow()
        points = list(shape.points)
        del points[row if 0 <= row < len(points) else -1]
        edges = len(points) if shape.closed else 2  # open: the start end, 0 or 1
        self.replace_rectangle(part, replace(shape, points=tuple(points), edge=min(shape.edge, max(edges - 1, 0))))

    def material_kerf(self, number):
        for material in self.materials:
            if material.number == number:
                return material.kerf
        return None

    def position_reference(self, placement):
        """Where the X and Y fields point: the drawn corner of a rectangle or the centre of a circle
        made in PlasmaNest, else the lower left corner of the cut path's bounds (which include kerf
        and lead-in)."""
        shape = shape_of(placement.program)
        if shape is not None:
            return placement.map_point(shape.anchor)
        return placement.bounds()[:2]

    POSITION_LABELS = {Rectangle: ("X (drawn corner):", "Y (drawn corner):"), Circle: ("X (centre):", "Y (centre):"),
                       HoleCircle: ("X (centre):", "Y (centre):"), Slot: ("X (centre):", "Y (centre):"),
                       Polygon: ("X (first point):", "Y (first point):"),
                       type(None): ("X (left edge):", "Y (bottom edge):")}

    def update_placement_fields(self):
        selected = self.selected_parts()
        shape = shape_of(selected[0].placement.program) if len(selected) == 1 else None
        self.show_rectangle_fields(shape, selected[0] if shape is not None else None)
        x_label, y_label = self.POSITION_LABELS[type(shape)]
        self.pos_x_label.setText(x_label)
        self.pos_y_label.setText(y_label)
        if len(selected) != 1:
            return
        x0, y0 = self.position_reference(selected[0].placement)
        self.updating_ui = True
        for spin, value in ((self.pos_x, x0), (self.pos_y, y0), (self.angle, selected[0].placement.angle)):
            if not spin.hasFocus():
                spin.setValue(value)
        self.updating_ui = False

    def selected_rectangle(self):
        """The selected part and the Rectangle or Circle it was drawn as, if just one is selected."""
        selected = self.selected_parts()
        if len(selected) != 1:
            return None, None
        return selected[0], shape_of(selected[0].placement.program)

    def rectangle_edited(self):
        """A field of the figure box changed: make the figure again with the new settings."""
        if self.updating_ui:
            return
        part, shape = self.selected_rectangle()
        if shape is None or part is not getattr(self, "fields_part", None):
            return  # the fields show another figure's values
        material = self.rect_material.currentData()
        kerf = self.material_kerf(material) if material is not None else None
        changes = {"material": material, "kerf": kerf if kerf is not None else self.rect_kerf.value(),
                   "leadin": self.rect_leadin.value()}
        if isinstance(shape, HoleCircle):
            changes.update(diameter=self.size_d.value(), holes=self.hole_count.value(),
                           hole_diameter=self.hole_d.value(), start=self.hole_start.value())
        elif isinstance(shape, Circle):
            changes.update(diameter=self.size_d.value(), inside=bool(self.rect_side.currentData()))
        elif isinstance(shape, Slot):
            changes.update(length=self.size_w.value(), width=self.size_h.value(),
                           inside=bool(self.rect_side.currentData()))
        elif isinstance(shape, Polygon):
            closed = self.poly_closed.isChecked()
            changes.update(closed=closed, inside=bool(self.rect_side.currentData()))
            if closed != shape.closed:  # the lead-in's place means something else: start afresh
                changes.update(edge=0, at=0.5)
        else:
            changes.update(width=self.size_w.value(), height=self.size_h.value(),
                           inside=bool(self.rect_side.currentData()))
        changed = type(shape)(**{**shape.__dict__, **changes})
        if changed == shape:
            self.show_rectangle_fields(shape, part)  # e.g. the kerf field after choosing a material
            return
        self.replace_rectangle(part, changed)

    def replace_rectangle(self, part, shape):
        """Put a remade figure in place of the old one. Its anchor (a rectangle's drawn corner, a
        circle's centre) stays put, also when rotated, and it keeps its place in the cutting order."""
        try:
            program = shape.program()
        except ValueError as error:
            self.rect_error.setText(str(error))
            self.rect_error.show()
            self.show_rectangle_fields(shape_of(part.placement.program), part)
            return None
        self.rect_error.hide()
        self.remember_rectangle(shape)
        old = part.placement
        anchor = old.map_point(shape.anchor)
        moved = Placement(program, 0.0, 0.0, old.angle).map_point(shape.anchor)
        placement = Placement(program, anchor[0] - moved[0], anchor[1] - moved[1], old.angle, old.cut)
        index = self.parts.index(part)
        # Without a selection for a moment the fields would hide and lose the keyboard focus.
        self.updating_ui = True
        try:
            self.remove_part(part)
            new_part = self.add_part(placement)
            self.parts.remove(new_part)
            self.parts.insert(index, new_part)
        finally:
            self.updating_ui = False
        self.after_change([new_part])
        return new_part

    def place_leadin(self):
        part, shape = self.selected_rectangle()
        if shape is not None:
            self.view.start_leadin(part)

    def set_leadin_position(self, part, position):
        shape = shape_of(part.placement.program)
        if shape is not None:
            self.replace_rectangle(part, shape.with_leadin_at(position))

    def remember_rectangle(self, shape):
        """New figures start with the settings used last."""
        if isinstance(shape, HoleCircle):
            self.hole_circle_defaults = {"holes": shape.holes, "hole_diameter": shape.hole_diameter}
            inside = self.rectangle_defaults["inside"]  # a hole circle says nothing about the side
        else:
            inside = shape.inside
        self.rectangle_defaults = {"inside": inside, "material": shape.material,
                                   "kerf": shape.kerf, "leadin": shape.leadin}

    def figure_defaults(self):
        settings = dict(self.rectangle_defaults)
        if settings["material"] is not None and self.material_kerf(settings["material"]) is not None:
            settings["kerf"] = self.material_kerf(settings["material"])
        return settings

    def new_rectangle(self, x0, y0, x1, y1):
        left, right = sorted((x0, x1))
        bottom, top = sorted((y0, y1))
        return Rectangle(left, bottom, right - left, top - bottom, **self.figure_defaults())

    def new_circle(self, cx, cy, diameter):
        return Circle(cx, cy, diameter, **self.figure_defaults())

    def new_polygon(self, points, closed):
        return Polygon(tuple((float(x), float(y)) for x, y in points), closed=closed, **self.figure_defaults())

    def new_slot(self, x0, y0, x1, y1):
        """The slot filling a drawn box, and the angle that lays it along the box's longer side."""
        w, h = abs(x1 - x0), abs(y1 - y0)
        slot = Slot((x0 + x1) / 2, (y0 + y1) / 2, max(w, h), min(w, h), **self.figure_defaults())
        return slot, (90.0 if h > w else 0.0)

    def new_hole_circle(self, cx, cy, diameter):
        settings = self.figure_defaults()
        del settings["inside"]
        return HoleCircle(cx, cy, diameter, **self.hole_circle_defaults, **settings)

    def placement_edited(self):
        if self.updating_ui:
            return
        selected = self.selected_parts()
        if len(selected) != 1:
            return
        part = selected[0]
        part.set_angle(self.angle.value())
        x0, y0 = self.position_reference(part.placement)
        part.set_center(part.placement.x + self.pos_x.value() - x0, part.placement.y + self.pos_y.value() - y0)
        self.refresh()
        self.commit()

    # --------------------------------------------------------------- refreshing

    def schedule_refresh(self):
        if not self.refresh_timer.isActive():
            self.refresh_timer.start()

    def refresh(self):
        self.refresh_timer.stop()
        self.check_collisions()
        self.update_travel()
        self.update_placement_fields()

    def cut_parts(self):
        return [part for part in self.parts if part.placement.cut]

    def check_collisions(self):
        """Overlaps and the sheet's edge, only for the figures to cut: the others may wait anywhere."""
        cut = self.cut_parts()
        regions = [(part, part.sceneBoundingRect(), part.scene_region()) for part in cut]
        overlapping = set()
        for i, (part_a, rect_a, region_a) in enumerate(regions):
            for part_b, rect_b, region_b in regions[i + 1:]:
                if rect_a.intersects(rect_b) and region_a.intersects(region_b) \
                        and not holes_within(part_b, region_b, region_a) \
                        and not holes_within(part_a, region_a, region_b) \
                        and not self.in_drawn_hole(part_a, region_a, part_b, region_b, regions) \
                        and not self.in_drawn_hole(part_b, region_b, part_a, region_a, regions):
                    overlapping.add(part_a)
                    overlapping.add(part_b)
        outside = 0
        for part in self.parts:
            if not part.placement.cut:
                part.set_state("off")
            elif not part.on_sheet(self.sheet):
                part.set_state("outside")
                outside += 1
            elif part in overlapping:
                part.set_state("overlap")
            else:
                part.set_state("ok")
        self.outside_count, self.overlap_count = outside, len(overlapping)
        if len(cut) == len(self.parts):
            messages = [plural(len(self.parts), "figure")]
        else:
            messages = [f"{len(cut)} of {plural(len(self.parts), 'figure')} to cut"]
        if outside:
            messages.append(f"<b style='color:{COLOR_OUTSIDE.name()}'>{outside} outside the sheet</b>")
        if overlapping:
            allowed = " (allowed)" if self.allow_overlaps.isChecked() else ""
            messages.append(f"<b style='color:{COLOR_OVERLAP.name()}'>{len(overlapping)} overlapping{allowed}</b>")
        self.status_label.setText(" · ".join(messages))

    @staticmethod
    def in_drawn_hole(part, region, other, other_region, regions):
        """True for a figure lying wholly in a hole drawn in PlasmaNest, when the other figure is
        that hole or the part the hole is in: a part nested in a hole, as autonest does."""
        for hole, rect, hole_region in regions:
            if hole is part:
                continue
            shape = shape_of(hole.placement.program)
            if shape is None or not shape.inside or not hole_region.contains(region):
                continue
            if hole is other or other_region.contains(hole_region):
                return True
        return False

    def update_travel(self):
        path = QPainterPath()
        if self.show_rapids.isChecked():
            previous = None
            for part in self.cut_parts():
                ends = [op.move for op in part.placement.program.ops if op.kind == "move"]
                first = part.placement.map_point(ends[0].end)
                if previous is not None:
                    path.moveTo(*previous)
                    path.lineTo(*first)
                previous = part.placement.map_point(ends[-1].end)
        self.travel.setPath(path)

    def rapids_toggled(self, checked):
        for part in self.parts:
            part.set_show_rapids(checked)
        self.update_travel()

    def rebuild_list(self):
        self.updating_ui = True
        self.part_list.clear()
        copy_numbers = {}
        for row, part in enumerate(self.parts, 1):
            name = part.placement.program.name
            copy_numbers[name] = copy_numbers.get(name, 0) + 1
            mirrored = ", mirrored" if part.placement.mirror else ""
            item = QListWidgetItem(f"{row}.  {name}  (copy {copy_numbers[name]}{mirrored})")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if part.placement.cut else Qt.Unchecked)
            item.setToolTip("Checked: cut. Unchecked: kept in the nest but not sent to QtPlasmaC.")
            self.part_list.addItem(item)
            item.setSelected(part.isSelected())
            part.set_number(row)
        self.updating_ui = False

    def fit_view(self):
        self.user_has_view = True
        self.view.fit()

    # --------------------------------------------------------------- operations

    def add_part(self, placement):
        part = PartItem(placement, self)
        part.set_show_rapids(self.show_rapids.isChecked())
        self.scene_.addItem(part)
        self.parts.append(part)
        return part

    def remove_part(self, part):
        self.parts.remove(part)
        self.scene_.removeItem(part)

    def find_free_spot(self, width, height):
        """Lowest, leftmost free position for a box, or beside the sheet if there is none."""
        boxes = [part.placement.bounds() for part in self.parts]
        sheet = self.sheet
        x0, y0, x1, y1 = sheet.bounds
        # A figure that only just fits is better placed tight against the edge than beside the sheet.
        for margin in (PART_GAP, 0.0):
            xs = {x0 + margin} | {b[2] + PART_GAP for b in boxes}
            ys = {y0 + margin} | {b[3] + PART_GAP for b in boxes}
            if not sheet.is_rectangle:
                # Corners of a polygon rarely line up with free space, so also try a coarse grid.
                xs |= {x + margin for x, _ in sheet.corners}
                ys |= {y + margin for _, y in sheet.corners}
                xs |= {x0 + margin + k * sheet.width / FREE_SPOT_GRID for k in range(FREE_SPOT_GRID)}
                ys |= {y0 + margin + k * sheet.height / FREE_SPOT_GRID for k in range(FREE_SPOT_GRID)}
            for y in sorted(ys):
                for x in sorted(xs):
                    if x + width > x1 or y + height > y1:
                        continue
                    if any(x < b[2] + PART_GAP and x + width + PART_GAP > b[0]
                           and y < b[3] + PART_GAP and y + height + PART_GAP > b[1] for b in boxes):
                        continue
                    if sheet.contains_box(x - margin, y - margin, x + width + margin, y + height + margin):
                        return x, y
        right =max([x1] + [b[2] for b in boxes]) + PART_GAP * 5
        return right, y0

    def place_free(self, placement):
        x0, y0, x1, y1 = placement.bounds()
        x, y = self.find_free_spot(x1 - x0, y1 - y0)
        placement.x += x - x0
        placement.y += y - y0

    def load_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Load G-code", "",
                                                "G-code (*.ngc *.nc *.tap *.gcode *.txt);;All files (*)")
        self.load_paths(paths)

    def load_paths(self, paths):
        added = []
        for path in paths:
            try:
                program = load_program(path)
            except (GCodeError, OSError) as error:
                QMessageBox.critical(self, "Cannot load the file", f"{os.path.basename(path)}\n\n{error}")
                continue
            if program.warnings:
                QMessageBox.warning(self, "Notes on the file",
                                    os.path.basename(path) + "\n\n" + "\n".join(program.warnings))
            placement = Placement(program)
            self.place_free(placement)
            added.append(self.add_part(placement))
        if added:
            self.after_change(added)

    def load_conversational(self, path=None):
        """Add the shapes accepted in QtPlasmaC's CONVERSATIONAL tab as one figure."""
        path = path or CONVERSATIONAL_FILE
        title = "Get from CONVERSATIONAL"
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            QMessageBox.information(self, title, "There is nothing from CONVERSATIONAL. Make a shape there "
                                                 "and accept it with ADD or ACCEPT.")
            return None
        if ";qtplasmac filtered g-code file" in text.lower():
            QMessageBox.information(self, title, "CONVERSATIONAL holds the file that was loaded in MAIN, "
                                                 "not a new shape. Press NEW in CONVERSATIONAL and make "
                                                 "the shape there.")
            return None
        name = "CONVERSATIONAL " + time.strftime("%H:%M:%S")
        try:
            program = Program(name, text)
        except GCodeError as error:
            message = str(error)
            if "no XY moves" in message or "no cutting moves" in message:
                message = "CONVERSATIONAL is empty. Make a shape there and accept it with ADD or ACCEPT."
            QMessageBox.critical(self, title, message)
            return None
        if program.warnings:
            QMessageBox.warning(self, title, "\n".join(program.warnings))
        placement = Placement(program)
        self.place_free(placement)
        part = self.add_part(placement)
        self.after_change([part])
        return part

    def copy_selection(self):
        selected = self.selected_parts()
        if not selected:
            return
        dialog = CopyDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.copy_parts(selected, dialog.count.value())

    def copy_parts(self, parts, count):
        parts = list(parts)
        added = []
        for _ in range(count):
            for part in parts:
                source = part.placement
                placement = Placement(source.program, source.x, source.y, source.angle, source.cut, source.mirror)
                self.place_free(placement)
                added.append(self.add_part(placement))
        self.after_change(added)

    def autonest_dialog(self):
        if not self.cut_parts():
            self.statusBar().showMessage("There are no figures to cut to autonest.", 5000)
            return
        dialog = AutonestDialog(self, self.autonest_settings,
                                any(p.placement.cut for p in self.selected_parts()))
        if dialog.exec_() == QDialog.Accepted:
            self.autonest_settings = dialog.settings()
            self.autonest_parts(**self.autonest_settings)

    def autonest_parts(self, gap=PART_GAP, margin=PART_GAP, turn=True, mirror=False):
        """Pack the selected figures to cut, or all of them, against the sheet's bottom edge. The
        other figures to cut stay where they are; figures not cut are neither moved nor avoided."""
        cut = self.cut_parts()
        selected = {id(p) for p in self.selected_parts()}
        # A hole drawn in a part moves with the part, also when only one of them is selected.
        moving = []
        for group in group_figures([p.placement for p in cut]):
            members = [cut[i] for i in group]
            if not selected or any(id(p) in selected for p in members):
                moving += members
        if not moving:
            return
        fixed = [p.placement for p in cut if p not in moving]
        self.view.stop_drawing()
        # Many different figures can take a few seconds.
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            # Drawn figures keep their cut direction and lead-in, as when mirrored by hand.
            moves, unplaced = autonest(self.sheet, [p.placement for p in moving], fixed, gap, margin, turn,
                                       mirror, lambda placement: shape_of(placement.program) is None)
        except ValueError as error:
            QMessageBox.warning(self, APP_NAME, str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
        parts = {id(p.placement): p for p in self.parts}
        mirrored = sum(1 for move in moves if move.mirror != move.placement.mirror)
        for move in moves:
            part = parts[id(move.placement)]
            part.set_angle(move.angle)
            part.set_mirror(move.mirror)
            part.set_center(move.x, move.y)
        # What lies inside a contour is cut first, while the sheet round it is still attached: a
        # part in a hole before the hole, a hole before the part's outline.
        by_placement = {id(p.placement): p for p in self.parts}
        old_order = list(self.parts)
        self.parts = [by_placement[id(p)] for p in inside_first([p.placement for p in self.parts])]
        reordered = sum(1 for a, b in zip(old_order, self.parts) if a is not b)
        self.after_change(moving)
        message = f"Autonest: {plural(len(moving) - len(unplaced), 'figure')} packed"
        in_holes = sum(1 for move in moves if move.host is not None)
        if in_holes:
            message += f", {in_holes} in holes"
        if mirrored:
            message += f", {mirrored} mirrored"
        if reordered:
            message += "; the cutting order now cuts what lies inside a contour first"
        if unplaced:
            message += f", {len(unplaced)} did not fit and lie beside the sheet"
        self.statusBar().showMessage(message + ".", 10000)
        return moves, unplaced

    def delete_selection(self):
        selected = self.selected_parts()
        for part in selected:
            self.remove_part(part)
        if selected:
            self.after_change()

    def delete_programs(self):
        programs = {id(p.placement.program) for p in self.selected_parts()}
        doomed = [p for p in self.parts if id(p.placement.program) in programs]
        for part in doomed:
            self.remove_part(part)
        if doomed:
            self.after_change()

    def reorder(self, direction):
        selected = self.selected_parts()
        order = self.parts if direction < 0 else list(reversed(self.parts))
        for index in range(1, len(order)):
            if order[index].isSelected() and not order[index - 1].isSelected():
                order[index - 1], order[index] = order[index], order[index - 1]
        self.parts = order if direction < 0 else list(reversed(order))
        if selected:
            self.after_change(selected)

    def rotate_selection(self, degrees):
        """Rotate the selection as one rigid group around the centre of its bounds."""
        selected = self.selected_parts()
        if not selected:
            return
        originals = [(p, p.placement.x, p.placement.y, p.placement.angle) for p in selected]
        self.rotate_parts(originals, self.selection_pivot(selected), degrees)
        self.refresh()
        self.commit()

    def selection_pivot(self, parts):
        """Centre of rotation: a figure's own centre, or the middle of a group's bounds."""
        if len(parts) == 1:
            return parts[0].placement.x, parts[0].placement.y
        boxes = [p.placement.bounds() for p in parts]
        return ((min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2,
                (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2)

    def rotate_parts(self, originals, pivot, degrees):
        """Set figures to their original (part, x, y, angle) turned by degrees around pivot."""
        radians = math.radians(degrees)
        c, s = math.cos(radians), math.sin(radians)
        for part, x, y, angle in originals:
            dx, dy = x - pivot[0], y - pivot[1]
            part.set_angle(angle + degrees)
            part.set_center(pivot[0] + c * dx - s * dy, pivot[1] + s * dx + c * dy)

    def mirror_selection(self, upside_down=False):
        """Mirror the selection as one rigid group about the same centre it rotates around: left to
        right, or top to bottom (upside down). Figures drawn in PlasmaNest are left alone: their cut
        direction and lead-in are part of the figure."""
        selected = self.selected_parts()
        if not selected:
            return False
        if any(part.drawn_shape is not None for part in selected):
            self.statusBar().showMessage("Figures drawn in PlasmaNest cannot be mirrored. Draw them the way "
                                         "they should be cut instead.", 10000)
            return False
        px, py = self.selection_pivot(selected)
        for part in selected:
            placement = part.placement
            # Flipping a turned figure on the sheet is the flip in its own coordinates turned the
            # other way: left-right gives -angle, upside down 180 - angle.
            part.set_angle(180.0 - placement.angle if upside_down else -placement.angle)
            part.set_mirror(not placement.mirror)
            if upside_down:
                part.set_center(placement.x, 2 * py - placement.y)
            else:
                part.set_center(2 * px - placement.x, placement.y)
        self.rebuild_list()
        self.refresh()
        self.commit()
        return True

    def nudge_selection(self, dx, dy):
        for part in self.selected_parts():
            part.moveBy(dx, dy)
        self.refresh()
        self.commit()

    @property
    def sheet(self):
        return self.sheet_item.sheet

    def sheet_changed(self):
        width, height = self.sheet_width.value(), self.sheet_height.value()
        if self.sheet.is_rectangle and (width, height) != (self.sheet.width, self.sheet.height):
            self.set_sheet(Sheet.rectangle(width, height, *self.sheet.bounds[:2]), fit=not self.user_has_view)

    def set_sheet(self, sheet, fit=True):
        self.sheet_item.set_sheet(sheet)
        self.update_sheet_panel()
        self.refresh()
        self.commit()
        if fit:
            self.view.fit()

    def update_sheet_panel(self):
        """Width and height edit a rectangle; for a polygon they only report its size."""
        sheet = self.sheet
        drawing = self.view.drawing is not None and self.view.drawing_target == "sheet"
        self.sheet_width.setValue(sheet.width)
        self.sheet_height.setValue(sheet.height)
        self.sheet_width.setEnabled(sheet.is_rectangle and not drawing)
        self.sheet_height.setEnabled(sheet.is_rectangle and not drawing)
        self.draw_button.setText("Cancel" if drawing else "Draw")
        self.corners_button.setEnabled(not drawing)
        self.machine_button.setEnabled(not drawing)
        self.rectangle_button.setEnabled(not sheet.is_rectangle and not drawing)
        if drawing:
            self.sheet_info.setText("Click the corners. Finish with a double click, Enter or a click on "
                                    "the first corner. Backspace removes the latest corner, Esc cancels. "
                                    "Shift gives straight edges.")
        elif sheet.is_rectangle:
            self.sheet_info.setText("")
        else:
            self.sheet_info.setText(f"Polygon with {len(sheet.corners)} corners, {sheet.area / 1e6:.2f} m². "
                                    "Width and height show its outer size.")

    def toggle_shape(self, kind):
        if self.view.shape_mode == kind:
            self.view.stop_shape()
        else:
            self.view.start_shape(kind)
            self.update_sheet_panel()
        self.update_shape_panel()

    def toggle_rectangle(self):
        self.toggle_shape("rectangle")

    def toggle_circle(self):
        self.toggle_shape("circle")

    def toggle_hole_circle(self):
        self.toggle_shape("hole circle")

    def toggle_slot(self):
        self.toggle_shape("slot")

    def toggle_polygon(self):
        view = self.view
        if view.drawing is not None and view.drawing_target == "polygon":
            view.stop_drawing()
        else:
            if view.drawing is not None:
                view.stop_drawing()
            view.start_drawing("polygon")
            self.statusBar().showMessage("Click the points. A click on the first point closes the polygon; a "
                                         "double click or Enter leaves it open. Backspace removes the latest "
                                         "point, Esc cancels, Shift gives straight edges.")
        self.update_sheet_panel()
        self.update_shape_panel()

    def update_shape_panel(self):
        mode = self.view.shape_mode
        self.rectangle_shape_button.setText("Cancel rectangle" if mode == "rectangle" else "Draw rectangle")
        self.circle_shape_button.setText("Cancel circle" if mode == "circle" else "Draw circle")
        self.hole_circle_shape_button.setText("Cancel hole circle" if mode == "hole circle" else "Draw hole circle")
        self.slot_shape_button.setText("Cancel slot" if mode == "slot" else "Draw slot")
        polygon = self.view.drawing is not None and self.view.drawing_target == "polygon"
        self.polygon_shape_button.setText("Cancel polygon" if polygon else "Draw polygon")

    def add_shape(self, program, angle=0.0):
        """A figure drawn in PlasmaNest, placed where it was drawn: turned about its anchor."""
        anchor = shape_of(program).anchor
        moved = Placement(program, 0.0, 0.0, angle).map_point(anchor)
        placement = Placement(program, anchor[0] - moved[0], anchor[1] - moved[1], angle)
        part = self.add_part(placement)
        self.after_change([part])
        shape = shape_of(program)
        if isinstance(shape, Polygon):
            self.statusBar().showMessage(f"{program.name} made. Change the points in the panel or drag them.",
                                         10000)
            return part
        self.statusBar().showMessage(f"{program.name} mm made. Type exact sizes in the panel if needed.",
                                     10000)
        # Ready for exact numbers: type the width, Tab, the height, Enter (or the diameter, Enter).
        field = self.size_d if isinstance(shape, (Circle, HoleCircle)) else self.size_w
        field.setFocus()
        field.selectAll()
        return part

    def toggle_drawing(self):
        if self.view.shape_mode is not None:
            self.view.stop_shape()
        if self.view.drawing is not None:
            was_sheet = self.view.drawing_target == "sheet"
            self.view.stop_drawing()
            if not was_sheet:
                self.view.start_drawing()
        else:
            self.view.start_drawing()
        self.update_sheet_panel()

    def edit_corners(self):
        dialog = CornerDialog(self, self.sheet.corners)
        if dialog.exec_() == QDialog.Accepted:
            self.set_sheet(dialog.sheet)

    def sheet_from_machine(self):
        dialog = MachineDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.set_sheet(dialog.sheet)

    def make_rectangle(self):
        self.set_sheet(Sheet.rectangle(self.sheet.width, self.sheet.height))

    def after_change(self, select=None):
        if select is not None:
            self.updating_ui = True
            self.scene_.clearSelection()
            for part in select:
                part.setSelected(True)
            self.updating_ui = False
        self.rebuild_list()
        self.selection_changed()
        self.refresh()
        self.commit()
        if not self.user_has_view:
            self.view.fit()

    # --------------------------------------------------------------------- undo

    def current_state(self):
        return (self.sheet,
                tuple((p.placement.program, round(p.placement.x, 6), round(p.placement.y, 6),
                       round(p.placement.angle, 6), p.placement.cut, p.placement.mirror) for p in self.parts),
                self.allow_overlaps.isChecked())

    def allow_overlaps_toggled(self, checked):
        if self.updating_ui:
            return
        self.refresh()
        self.commit()

    def set_allow_overlaps(self, allow):
        updating = self.updating_ui
        self.updating_ui = True
        try:
            self.allow_overlaps.setChecked(allow)
        finally:
            self.updating_ui = updating

    def commit(self):
        """Record an undo step if anything changed since the last one."""
        state = self.current_state()
        if self.undo_index >= 0 and state == self.undo_stack[self.undo_index]:
            return
        del self.undo_stack[self.undo_index + 1:]
        self.undo_stack.append(state)
        self.undo_index = len(self.undo_stack) - 1
        self.update_title()

    def restore(self, state):
        sheet, figures, allow_overlaps = state
        self.set_allow_overlaps(allow_overlaps)
        self.view.stop_drawing()
        self.sheet_item.set_sheet(sheet)
        self.update_sheet_panel()
        for part in list(self.parts):
            self.remove_part(part)
        for program, x, y, angle, cut, mirror in figures:
            self.add_part(Placement(program, x, y, angle, cut, mirror))
        self.rebuild_list()
        self.selection_changed()
        self.refresh()
        self.update_title()

    def undo(self):
        if self.undo_index > 0:
            self.undo_index -= 1
            self.restore(self.undo_stack[self.undo_index])

    def redo(self):
        if self.undo_index < len(self.undo_stack) - 1:
            self.undo_index += 1
            self.restore(self.undo_stack[self.undo_index])

    # -------------------------------------------------------------------- files

    def is_dirty(self):
        return self.undo_index >= 0 and self.undo_stack[self.undo_index] != self.saved_state

    def update_title(self):
        name = os.path.basename(self.project_path) if self.project_path else "Untitled"
        self.setWindowTitle(f"{'*' if self.is_dirty() else ''}{name} - {APP_NAME}")
        # Called after every change and every undo and redo.
        if getattr(self, "live_dir", None):
            self.write_live_export()

    # ------------------------------------------------------------- live export

    def enable_live_export(self, directory):
        """Embedded in QtPlasmaC: keep the nest exported for the tab to load when going to MAIN."""
        self.live_dir = directory
        self.live_state = None
        self.autoload.show()
        if conversational_disabled():
            self.conversational_button.hide()
        self.write_live_export()

    def live_problems(self):
        problems = []
        if self.outside_count:
            problems.append(f"{plural(self.outside_count, 'figure')} outside the sheet.")
        if self.overlap_count and not self.allow_overlaps.isChecked():
            problems.append(f"{plural(self.overlap_count, 'figure')} overlapping each other.")
        warning = material_warning([p.placement for p in self.cut_parts()])
        if warning:
            problems.append(warning)
        return problems

    def write_live_export(self):
        if not getattr(self, "live_dir", None):
            return
        state = (self.current_state(), self.autoload.isChecked())
        if state == self.live_state:
            return
        try:
            self.refresh()
            placements = [p.placement for p in self.cut_parts()]
            gcode = export_gcode(placements) if placements else None
            live.write(self.live_dir, gcode, len(placements), self.live_problems(), self.autoload.isChecked())
            self.live_state = state
        except Exception as error:  # never let the export stop the user's work
            self.statusBar().showMessage(f"Could not update the nest for QtPlasmaC: {error}", 15000)

    def confirm_discard(self):
        if not self.is_dirty():
            return True
        answer = QMessageBox.question(self, APP_NAME, "Save the changes to the project?",
                                      QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if answer == QMessageBox.Save:
            return self.save()
        return answer == QMessageBox.Discard

    def reset_history(self):
        self.undo_stack = []
        self.undo_index = -1
        self.commit()
        self.saved_state = self.undo_stack[0]
        self.update_title()

    def new_project(self):
        if not self.confirm_discard():
            return
        for part in list(self.parts):
            self.remove_part(part)
        self.set_allow_overlaps(False)
        self.project_path = None
        self.rebuild_list()
        self.selection_changed()
        self.refresh()
        self.reset_history()

    def open_project(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "PlasmaNest project (*.pnest)")
        if path:
            self.open_project_path(path)

    def open_project_path(self, path):
        try:
            sheet, placements, options = load_project(path, with_options=True)
        except (OSError, ValueError, KeyError, IndexError, TypeError, GCodeError) as error:
            QMessageBox.critical(self, "Cannot open the project", str(error))
            return
        self.view.stop_drawing()
        for part in list(self.parts):
            self.remove_part(part)
        self.sheet_item.set_sheet(sheet)
        self.update_sheet_panel()
        self.set_allow_overlaps(options["allow_overlaps"])
        for placement in placements:
            self.add_part(placement)
        self.project_path = path
        self.rebuild_list()
        self.selection_changed()
        self.refresh()
        self.reset_history()
        self.view.fit()

    def save(self):
        if not self.project_path:
            return self.save_as()
        return self.write_project(self.project_path)

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save project", self.project_path or "nest.pnest",
                                              "PlasmaNest project (*.pnest)")
        if not path:
            return False
        if not path.lower().endswith(".pnest"):
            path += ".pnest"
        return self.write_project(path)

    def write_project(self, path):
        try:
            save_project(path, self.sheet, [p.placement for p in self.parts], self.allow_overlaps.isChecked())
        except OSError as error:
            QMessageBox.critical(self, "Cannot save", str(error))
            return False
        self.project_path = path
        self.saved_state = self.undo_stack[self.undo_index]
        self.update_title()
        return True

    def export(self):
        if not self.parts:
            QMessageBox.information(self, APP_NAME, "There are no figures to export.")
            return
        if not self.cut_parts():
            QMessageBox.information(self, APP_NAME, "No figure is checked for cutting in the list.")
            return
        self.refresh()
        placements = [p.placement for p in self.cut_parts()]
        problems = []
        if self.outside_count:
            problems.append(f"{plural(self.outside_count, 'figure')} outside the sheet.")
        if self.overlap_count and not self.allow_overlaps.isChecked():
            problems.append(f"{plural(self.overlap_count, 'figure')} overlapping each other.")
        warning = material_warning(placements)
        if warning:
            problems.append(warning)
        if problems:
            answer = QMessageBox.warning(self, "Check before exporting",
                                         "\n".join(problems) + "\n\nExport anyway?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        path, _ = QFileDialog.getSaveFileName(self, "Export to QtPlasmaC", self.export_suggestion(),
                                              "G-code (*.ngc)")
        if path:
            self.export_to(path)

    @staticmethod
    def export_dir():
        """The directory QtPlasmaC's latest-file button reads, or None if LinuxCNC's is missing."""
        return os.path.join(NC_FILES, EXPORT_DIR_NAME) if os.path.isdir(NC_FILES) else None

    def export_suggestion(self):
        directory = self.export_dir()
        if directory is None:
            return os.path.splitext(self.project_path)[0] + ".ngc" if self.project_path else "nest.ngc"
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            return "nest.ngc"
        name = os.path.splitext(os.path.basename(self.project_path))[0] if self.project_path else "nest"
        return os.path.join(directory, name + ".ngc")

    def export_to(self, path):
        if not path.lower().endswith(".ngc"):
            path += ".ngc"
        try:
            # LinuxCNC runs on Linux: always plain \n line endings.
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(export_gcode([p.placement for p in self.cut_parts()]))
        except OSError as error:
            QMessageBox.critical(self, "Cannot export", str(error))
            return False
        message = f"Exported {plural(len(self.cut_parts()), 'figure')} to {path}"
        directory = self.export_dir()
        if directory is not None:
            if os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(directory):
                message += ". Open it in QtPlasmaC with the latest-file user button."
            else:
                message += f". The latest-file user button in QtPlasmaC only finds files in {directory}."
        self.statusBar().showMessage(message, 15000)
        return True

    def closeEvent(self, event):
        if self.confirm_discard():
            event.accept()
        else:
            event.ignore()


def conversational_disabled(run=None):
    """True if QtPlasmaC's CONVERSATIONAL tab is switched off with qtplasmac.conv_disable."""
    import subprocess
    run = run or subprocess.run
    try:
        result = run(["halcmd", "getp", "qtplasmac.conv_disable"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip().upper() == "TRUE"


def announce_window(window, stream=None):
    """Embedded mode: tell the host our X11 window id on the first line, then go quiet.

    QtVCP reads one line from our stdout and never empties the pipe again, so anything
    printed later could fill it and freeze PlasmaNest.
    """
    stream = stream or sys.stdout
    stream.write(f"{int(window.winId())}\n")
    stream.flush()
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, stream.fileno())
    os.close(devnull)


def autosave(window, path=AUTOSAVE_FILE):
    """Keep unsaved work when PlasmaNest is ended without a chance to ask."""
    if not window.is_dirty():
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        save_project(path, window.sheet, [p.placement for p in window.parts], window.allow_overlaps.isChecked())
    except OSError:
        return None
    return path


def x11_offset(window):
    """How far Qt's idea of the window's screen position is from the real one, or None.

    An embedded window is a child of the host's window, but Qt still treats it as a top-level
    window and maps to the screen from its position inside the host.
    """
    try:
        from Xlib import display
    except ImportError:
        return None
    try:
        connection = display.Display()
        try:
            root = connection.screen().root
            ours = connection.create_resource_object("window", int(window.winId()))
            real = root.translate_coords(ours, 0, 0)
            return QPoint(real.x, real.y) - window.mapToGlobal(QPoint(0, 0))
        finally:
            connection.close()
    except Exception:
        return None


class PopupPlacer(QObject):
    """Embedded mode: move menus and drop-down lists opened from the main window to the right place.

    Qt places them from the main window's screen position, which is wrong when embedded.
    Sub-menus are placed from their parent menu, which is already right, so they are left alone.
    """

    def __init__(self, window, offset=x11_offset):
        super().__init__(window)
        self.window = window
        self.offset = offset

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() \
                and obj.windowType() == Qt.Popup:
            parent = obj.parentWidget()
            if parent is not None and parent.window() is self.window:
                offset = self.offset(self.window)
                if offset is not None and not offset.isNull():
                    obj.move(obj.pos() + offset)
        return False


def x11_has_host(window):
    """True when the window has been moved into another window, False when it is still a
    top-level window, None when X11 cannot be asked."""
    try:
        from Xlib import display
    except ImportError:
        return None
    try:
        connection = display.Display()
        try:
            ours = connection.create_resource_object("window", int(window.winId()))
            return ours.query_tree().parent.id != connection.screen().root.id
        finally:
            connection.close()
    except Exception:
        return None


def show_when_embedded(window, has_host=x11_has_host, poll_ms=50, timeout_ms=10000):
    """Show the window only once the host has taken it in.

    Shown earlier, as a top-level window, the window manager may frame it before the host's
    container takes it, and PlasmaNest ends up in a window of its own. Inside another window
    the window manager leaves it alone. If no host takes it in time, show it on its own anyway.
    """
    waited = [0]
    timer = QTimer(window)

    def check():
        waited[0] += poll_ms
        host = has_host(window)
        if host or host is None or waited[0] >= timeout_ms:
            timer.stop()
            window.show()
    timer.timeout.connect(check)
    timer.start(poll_ms)
    window._host_timer = timer
    return timer


def stop_with_host(app, window, stdin_fd=0):
    """Quit without dialogs when the host ends: on SIGTERM, as QtVCP's _hal_cleanup sends, or when
    the host's pipe on stdin closes because it died. Unsaved work is saved first."""
    def stop():
        autosave(window)
        app.quit()
    signal.signal(signal.SIGTERM, lambda *args: QTimer.singleShot(0, stop))
    # Python only runs signal handlers when it gets control, so wake it up now and then.
    timer = QTimer(app)
    timer.timeout.connect(lambda: None)
    timer.start(250)
    notifier = QSocketNotifier(stdin_fd, QSocketNotifier.Read, app)

    def stdin_ready():
        try:
            data = os.read(stdin_fd, 4096)
        except OSError:
            data = b""
        if not data:
            notifier.setEnabled(False)
            stop()
    notifier.activated.connect(lambda fd: stdin_ready())
    app._host_watch = (timer, notifier)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    embed = "--embed" in argv[1:]
    paths = [a for a in argv[1:] if a != "--embed"]
    app = QApplication(argv[:1])
    app.setApplicationName(APP_NAME)
    apply_theme(app, qtplasmac_colors())
    window = MainWindow()
    if embed:
        announce_window(window)
        stop_with_host(app, window)
        window.enable_live_export(live.live_dir())
        app._popup_placer = PopupPlacer(window)
        app.installEventFilter(app._popup_placer)
        # Shown once inside the host: a hidden widget is never painted, even in the host's container.
        show_when_embedded(window)
    else:
        window.show()
    projects = [p for p in paths if p.lower().endswith(".pnest")]
    if projects:
        window.open_project_path(projects[0])
    window.load_paths([p for p in paths if not p.lower().endswith(".pnest")])
    window.view.fit()
    result = app.exec_()
    # Tear the window down while QApplication still exists. Left to Python, the two go in any
    # order, and the window going after the application crashes now and then on quitting.
    window.updating_ui = True
    sip.delete(window)
    return result
