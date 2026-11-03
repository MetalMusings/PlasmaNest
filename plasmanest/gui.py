"""Qt user interface. The scene uses G-code coordinates in mm; the view flips Y."""

import math
import os
import signal
import sys
import time

from PyQt5.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, QSocketNotifier, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QFontMetrics, QKeySequence, QMouseEvent, QPainter, QPainterPath, QPainterPathStroker,
                         QPen, QPolygonF, QTransform)
from PyQt5.QtWidgets import (QAbstractItemView, QAction, QApplication, QCheckBox, QDialog,
                             QDialogButtonBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                             QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsSimpleTextItem,
                             QGraphicsView, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
                             QMainWindow, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget,
                             QTableWidgetItem, QToolTip, QVBoxLayout, QWidget)

from . import live, machine
from .gcode import GCodeError, Program, load_program
from .nest import Placement, Sheet, export_gcode, load_project, material_warning, save_project
from .shapes import rectangle_of, rectangle_program

APP_NAME = "PlasmaNest"
PART_GAP = 10.0        # mm between figures placed automatically
FREE_SPOT_GRID = 24    # extra positions per axis tried on a polygon sheet
CLOSE_POLYGON_RADIUS = 10  # pixels from the first corner that closes a drawn polygon
NUDGE_STEP = 1.0       # mm per arrow key press
NUDGE_STEP_LARGE = 10.0
HANDLE_DISTANCE = 42   # pixels from the figure to the rotation handle
ROTATE_SNAP = 15.0     # degrees with Ctrl held
ROTATE_MIN_RADIUS = 15 # pixels from the pivot; closer than this the angle is too jumpy

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


def cosmetic_pen(color, width=1.0, style=Qt.SolidLine):
    pen = QPen(color, width, style)
    pen.setCosmetic(True)
    return pen


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
        self.setToolTip("G-kodens X0 Y0")
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
        self.setToolTip("Dra för att rotera. Håll Ctrl för steg om 15°.")
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
        program = placement.program
        cx, cy = program.center
        self.cut_path = QPainterPath()
        self.cut_path.setFillRule(Qt.OddEvenFill)
        self.rapid_path = QPainterPath()
        for path in program.paths:
            polygon = QPolygonF([QPointF(x - cx, y - cy) for x, y in path.points])
            target = self.cut_path if path.kind == "cut" else self.rapid_path
            target.addPolygon(polygon)
        self.rect = self.cut_path.boundingRect()

        stroker = QPainterPathStroker()
        stroker.setWidth(6.0)
        self.hit_shape = QPainterPath(self.cut_path)
        self.hit_shape.addPath(stroker.createStroke(self.cut_path))

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
        self.label.setBrush(QBrush(QColor("#263238")))

        self.setPos(placement.x, placement.y)
        self.setRotation(placement.angle)

    def set_number(self, number):
        self.label.setText(str(number))
        self.label.setPos(-self.label.boundingRect().width() / 2, 0)

    def set_angle(self, angle):
        self.placement.angle = normalize_angle(angle)
        self.setRotation(self.placement.angle)

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
        return self.sceneTransform().map(self.cut_path)

    def on_sheet(self, sheet):
        # A polygon check walks every cut segment, and refresh runs for every figure while dragging.
        key = (self.placement.x, self.placement.y, self.placement.angle, sheet)
        if self.on_sheet_cache[0] != key:
            self.on_sheet_cache = (key, sheet.contains_placement(self.placement))
        return self.on_sheet_cache[1]

    def boundingRect(self):
        rect = self.cut_path.boundingRect().united(self.rapid_path.boundingRect())
        return rect.adjusted(-2, -2, 2, 2)

    def shape(self):
        return self.hit_shape

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
        color = {"ok": COLOR_CUT, "overlap": COLOR_OVERLAP, "outside": COLOR_OUTSIDE}[self.state]
        if selected and self.state == "ok":
            color = COLOR_SELECTED
        fill = QColor(color)
        fill.setAlpha(70 if selected else 35)
        painter.fillPath(self.cut_path, fill)
        if self.show_rapids and not self.rapid_path.isEmpty():
            painter.setPen(cosmetic_pen(COLOR_RAPID, 1.0, Qt.DashLine))
            painter.drawPath(self.rapid_path)
        painter.setPen(cosmetic_pen(color, 2.0 if selected else 1.4))
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
        painter.setBrush(QBrush(QColor(255, 255, 255, 230)))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QPen(QColor("#263238")))
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
        self.drawing = None
        self.draft = QGraphicsPathItem()
        self.draft.setPen(cosmetic_pen(COLOR_DRAFT, 2.0))
        self.draft.setAcceptedMouseButtons(Qt.NoButton)
        self.draft.setZValue(10)
        scene.addItem(self.draft)
        self.label = DraftLabel()
        scene.addItem(self.label)
        self.shape_mode = None   # "rectangle" while a new figure is being drawn
        self.shape_start = None  # its first corner, in scene mm
        self.shape_press = None  # view position of that press, to tell a drag from a click

    # ------------------------------------------------------------ polygon drawing

    def start_drawing(self):
        if self.shape_mode is not None:
            self.stop_shape()
        self.scene().clearSelection()
        self.drawing = []
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self.update_draft(None)

    def stop_drawing(self):
        self.drawing = None
        self.unsetCursor()
        self.draft.setPath(QPainterPath())

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

    def finish_drawing(self):
        try:
            sheet = Sheet(self.drawing)
        except ValueError as error:
            # Keep the corners: the last ones can be taken back with Backspace.
            self.window_.sheet_info.setText(f"<b style='color:{COLOR_OUTSIDE.name()}'>{error}</b>")
            return
        self.stop_drawing()
        self.window_.set_sheet(sheet)

    def drawing_key(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish_drawing()
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

    def start_rectangle(self):
        self.stop_drawing()
        self.scene().clearSelection()
        self.shape_mode = "rectangle"
        self.shape_start = None
        self.shape_press = None
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self.window_.statusBar().showMessage("Tryck i första hörnet och dra till motsatta hörnet, eller klicka "
                                             "i båda. Esc avbryter.")

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

    def update_shape(self, pos):
        x, y = self.snap(pos)
        if self.shape_start is None:
            self.draft.setPath(QPainterPath())
            self.label.set_text(f"X {format_mm(x)}   Y {format_mm(y)}", QPointF(x, y))
            return
        x0, y0 = self.shape_start
        path = QPainterPath()
        path.addRect(QRectF(QPointF(min(x0, x), min(y0, y)), QPointF(max(x0, x), max(y0, y))))
        self.draft.setPath(path)
        self.label.set_text(f"{format_mm(abs(x - x0))} × {format_mm(abs(y - y0))} mm", QPointF(x, y))

    def finish_shape(self, pos):
        x0, y0 = self.shape_start
        x1, y1 = self.snap(pos)
        try:
            program = rectangle_program(x0, y0, x1, y1)
        except ValueError:
            return False  # too small: keep waiting for the opposite corner
        self.stop_shape()
        self.window_.add_shape(program)
        return True

    def shape_press_event(self, event):
        if event.button() != Qt.LeftButton:
            self.stop_shape()
            return
        if self.shape_start is None:
            self.shape_start = self.snap(event.pos())
            self.shape_press = event.pos()
            self.update_shape(event.pos())
        else:
            self.finish_shape(event.pos())

    def shape_release_event(self, event):
        # A drag ends at the release; a click leaves the first corner set for a second click.
        if self.shape_start is not None and self.shape_press is not None:
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
            text = f"Vinkel {drag['originals'][0][0].placement.angle:.1f}°"
        else:
            text = f"Roterar {degrees:+.1f}°"
        self.window_.statusBar().showMessage(text)

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

    def mouseDoubleClickEvent(self, event):
        if self.drawing is not None and event.button() == Qt.LeftButton:
            # The first click of the double click has already placed the last corner.
            self.finish_drawing()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
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


class CopyDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Kopiera figur")
        self.count = QSpinBox()
        self.count.setRange(1, 500)
        self.count.setValue(1)
        form = QFormLayout(self)
        form.addRow("Antal kopior:", self.count)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class CornerDialog(QDialog):
    """Type the corners of the sheet in order around its edge."""

    def __init__(self, parent, corners):
        super().__init__(parent)
        self.setWindowTitle("Plåtens hörn")
        self.sheet = None
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["X (mm)", "Y (mm)"])
        self.table.horizontalHeader().setStretchLastSection(True)
        for corner in corners:
            self.add_row(self.table.rowCount(), corner)
        add_button = QPushButton("Lägg till hörn")
        add_button.clicked.connect(lambda checked=False: self.add_after_selected())
        remove_button = QPushButton("Ta bort hörn")
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
        layout.addWidget(QLabel("Ange hörnen i ordning runt plåten."))
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
                    raise ValueError(f"Rad {row + 1}: \"{text}\" är inget tal.")
                if not math.isfinite(value):
                    raise ValueError(f"Rad {row + 1}: \"{text}\" är inget tal.")
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
        painter.fillRect(self.rect(), QColor("white"))
        painter.setPen(QPen(COLOR_GRID))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        if not self.corners:
            painter.setPen(QPen(COLOR_VIEW))
            painter.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap,
                             "Inga punkter än.\nSpara första hörnet i QtPlasmaC.")
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
    """Sheet corners saved from QtPlasmaC with the Spara punkt button, live as they arrive."""

    POLL_MS = 500

    def __init__(self, parent, path=machine.POINTS_FILE, offsets=machine.current_offsets):
        super().__init__(parent)
        self.setWindowTitle("Plåt från maskinen")
        self.path = path
        self.read_offsets = offsets
        self.points = []
        self.offsets = None
        self.file_state = False  # never equal to a real state, so the first poll reads the file
        self.error = None
        self.sheet = None

        intro = QLabel("Jogga till plåtens hörn i ordning runt plåten och tryck <b>Spara punkt</b> i "
                       "QtPlasmaC vid varje hörn. Med lasern på sparas punkten under lasern.")
        intro.setWordWrap(True)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["X (mm)", "Y (mm)", "Sikte", "Sparad"])
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
        margin_row.addWidget(QLabel("Säkerhetsmarginal från kanten:"))
        margin_row.addWidget(self.margin)
        remove_button = QPushButton("Ta bort punkt")
        remove_button.clicked.connect(lambda checked=False: self.remove_selected())
        clear_button = QPushButton("Rensa alla")
        clear_button.clicked.connect(lambda checked=False: self.clear())
        row = QHBoxLayout()
        row.addWidget(remove_button)
        row.addWidget(clear_button)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Använd som plåt")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.preview = PointsPreview()
        self.preview.pointClicked.connect(lambda index: self.table.setCurrentCell(index, 0))
        self.table.currentCellChanged.connect(lambda *args: self.update_preview())
        legend = QLabel(f"<span style='color:{COLOR_DRAFT.name()}'>●</span> senaste punkten &nbsp; "
                        f"<span style='color:{COLOR_SELECTED.name()}'>▨</span> plåt efter marginalen")
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
            self.coordinates.setText("LinuxCNC svarar inte. Punkterna visas med det origo som gällde när "
                                     "de sparades.")
        else:
            o = self.offsets
            text = f"Arbetskoordinater i {o.name} med nuvarande origo (X {o.g5x[0]:.2f} Y {o.g5x[1]:.2f}"
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
            message = f"{len(self.points)} punkter sparade. Det behövs minst tre."
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
            self.result.setText(f"Plåt med {len(s.corners)} hörn, {s.width:.1f} × {s.height:.1f} mm, "
                                f"{s.area / 1e6:.2f} m² efter marginalen.")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(self.sheet is not None)
        self.update_preview()

    def update_preview(self):
        self.preview.set_data(self.corners() if not self.error else [], self.sheet, self.table.currentRow())

    def rewrite(self, points):
        try:
            machine.write_points(points, self.path)
        except OSError as error:
            QMessageBox.warning(self, APP_NAME, f"Kunde inte skriva punktfilen:\n{error}")
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
        self.sheet_item = SheetItem(Sheet.rectangle(3000.0, 1500.0))
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

        self._build_panel()
        self._build_actions()
        self.scene_.selectionChanged.connect(self.selection_changed)
        self.statusBar().showMessage("Läs in en G-kodsfil för att börja.")
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

        sheet_box = QGroupBox("Stålplåt (mm)")
        sheet_form = QFormLayout(sheet_box)
        self.sheet_width = self._length_spin(1, 100000, self.sheet.width)
        self.sheet_height = self._length_spin(1, 100000, self.sheet.height)
        self.sheet_width.editingFinished.connect(self.sheet_changed)
        self.sheet_height.editingFinished.connect(self.sheet_changed)
        sheet_form.addRow("Bredd (X):", self.sheet_width)
        sheet_form.addRow("Höjd (Y):", self.sheet_height)
        row = QHBoxLayout()
        self.draw_button = self._button("Rita", self.toggle_drawing, row)
        self.corners_button = self._button("Hörn…", self.edit_corners, row)
        self.rectangle_button = self._button("Rektangel", self.make_rectangle, row)
        sheet_form.addRow(row)
        row = QHBoxLayout()
        self.machine_button = self._button("Från maskinen…", self.sheet_from_machine, row)
        sheet_form.addRow(row)
        self.sheet_info = QLabel()
        self.sheet_info.setWordWrap(True)
        sheet_form.addRow(self.sheet_info)
        layout.addWidget(sheet_box)
        self.update_sheet_panel()

        parts_box = QGroupBox("Figurer (i skärordning)")
        parts_layout = QVBoxLayout(parts_box)
        self.part_list = CompactList()
        self.part_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.part_list.itemSelectionChanged.connect(self.list_selection_changed)
        parts_layout.addWidget(self.part_list)
        row = QHBoxLayout()
        self.load_button = self._button("Läs in…", self.load_files, row)
        self.copy_button = self._button("Kopiera…", self.copy_selection, row)
        parts_layout.addLayout(row)
        row = QHBoxLayout()
        self.rectangle_shape_button = self._button("Rita rektangel", self.toggle_rectangle, row)
        self.rectangle_shape_button.setToolTip("Tryck i ett hörn och dra till motsatta hörnet.")
        parts_layout.addLayout(row)
        row = QHBoxLayout()
        self.conversational_button = self._button("Hämta från CONVERSATIONAL", self.load_conversational, row)
        self.conversational_button.setToolTip("Lägger till figurerna som godkänts i QtPlasmaC:s CONVERSATIONAL.")
        self.conversational_button.setVisible(os.path.isdir(os.path.dirname(CONVERSATIONAL_FILE)))
        parts_layout.addLayout(row)
        row = QHBoxLayout()
        self.delete_button = self._button("Ta bort", self.delete_selection, row)
        self.delete_all_button = self._button("Ta bort hela G-koden", self.delete_programs, row)
        parts_layout.addLayout(row)
        row = QHBoxLayout()
        self.up_button = self._button("Skär tidigare", lambda: self.reorder(-1), row)
        self.down_button = self._button("Skär senare", lambda: self.reorder(1), row)
        parts_layout.addLayout(row)
        layout.addWidget(parts_box, 1)

        place_box = QGroupBox("Placering")
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
        place_form.addRow("X (vänster kant):", self.pos_x)
        place_form.addRow("Y (nedre kant):", self.pos_y)
        place_form.addRow("Vinkel:", self.angle)
        # Only for a rectangle drawn in PlasmaNest: its exact size.
        self.size_w = self._length_spin(1, 100000, 100)
        self.size_h = self._length_spin(1, 100000, 100)
        self.size_w_label, self.size_h_label = QLabel("Bredd:"), QLabel("Höjd:")
        self.size_w.editingFinished.connect(self.size_edited)
        self.size_h.editingFinished.connect(self.size_edited)
        place_form.addRow(self.size_w_label, self.size_w)
        place_form.addRow(self.size_h_label, self.size_h)
        self.show_size_fields(None)
        row = QHBoxLayout()
        self.rotate_step = QDoubleSpinBox()
        self.rotate_step.setRange(0.01, 180)
        self.rotate_step.setValue(90)
        self.rotate_step.setSuffix(" °")
        self.rotate_ccw = self._button("↺", lambda: self.rotate_selection(self.rotate_step.value()), row)
        self.rotate_cw = self._button("↻", lambda: self.rotate_selection(-self.rotate_step.value()), row)
        row.addWidget(self.rotate_step)
        place_form.addRow("Rotera:", row)
        self.place_box = place_box
        layout.addWidget(place_box)

        self.show_rapids = QCheckBox("Visa snabbrörelser (G0)")
        self.show_rapids.setChecked(True)
        self.show_rapids.toggled.connect(self.rapids_toggled)
        layout.addWidget(self.show_rapids)

        # Only shown when embedded in QtPlasmaC, see enable_live_export.
        self.autoload = QCheckBox("Läs in i QtPlasmaC vid byte till MAIN")
        self.autoload.setChecked(True)
        self.autoload.setToolTip("Nesten läses in i förhandsvisningen när du går från PLASMANEST till MAIN.")
        self.autoload.toggled.connect(lambda checked: self.write_live_export())
        self.autoload.hide()
        layout.addWidget(self.autoload)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        help_label = QLabel("Vänsterdrag: flytta eller markera.\n"
                            "Högerdrag på figur: rotera (Ctrl: steg om 15°).\n"
                            "Hjul: zooma. Shift+hjul: rotera.\n"
                            "Högerdrag på tom yta eller mittendrag: panorera.\n"
                            "Pilar: flytta 1 mm (Shift: 10 mm).")
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #607d8b;")
        layout.addWidget(help_label)

        dock = QDockWidget("Verktyg", self)
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
        file_menu = self.menuBar().addMenu("&Arkiv")
        edit_menu = self.menuBar().addMenu("&Redigera")
        view_menu = self.menuBar().addMenu("&Visa")
        toolbar = self.addToolBar("Verktyg")
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

        action(file_menu, "&Nytt projekt", self.new_project, QKeySequence.New)
        action(file_menu, "&Öppna projekt…", self.open_project, QKeySequence.Open, True)
        action(file_menu, "&Spara projekt", self.save, QKeySequence.Save, True)
        action(file_menu, "Spara projekt &som…", self.save_as, QKeySequence.SaveAs)
        file_menu.addSeparator()
        action(file_menu, "&Läs in G-kod…", self.load_files, "Ctrl+L", True)
        self.export_action = action(file_menu, "&Exportera till QtPlasmaC (.ngc)…", self.export, "Ctrl+E", True)
        file_menu.addSeparator()
        action(file_menu, "A&vsluta", self.close, QKeySequence.Quit)

        self.undo_action = action(edit_menu, "Ån&gra", self.undo, QKeySequence.Undo)
        self.redo_action = action(edit_menu, "Gör &om", self.redo, QKeySequence.Redo)
        edit_menu.addSeparator()
        action(edit_menu, "&Kopiera figur…", self.copy_selection, "Ctrl+D")
        action(edit_menu, "&Ta bort figur", self.delete_selection, QKeySequence.Delete)
        action(edit_menu, "Markera &alla", self.select_all, QKeySequence.SelectAll)
        action(edit_menu, "&Rotera", lambda: self.rotate_selection(self.rotate_step.value()), "R")

        action(view_menu, "&Anpassa till fönstret", self.fit_view, "F", True)

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
                       self.up_button, self.down_button, self.rotate_ccw, self.rotate_cw):
            widget.setEnabled(has_selection)
        self.place_box.setEnabled(has_selection)
        self.pos_x.setEnabled(len(selected) == 1)
        self.pos_y.setEnabled(len(selected) == 1)
        self.angle.setEnabled(len(selected) == 1)
        self.update_handles()
        self.update_placement_fields()

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

    def show_size_fields(self, rectangle):
        for widget in (self.size_w_label, self.size_w, self.size_h_label, self.size_h):
            widget.setVisible(rectangle is not None)
        if rectangle is not None:
            self.updating_ui = True
            for spin, value in ((self.size_w, rectangle[2]), (self.size_h, rectangle[3])):
                if not spin.hasFocus():
                    spin.setValue(value)
            self.updating_ui = False

    def update_placement_fields(self):
        selected = self.selected_parts()
        self.show_size_fields(rectangle_of(selected[0].placement.program) if len(selected) == 1 else None)
        if len(selected) != 1:
            return
        x0, y0, _, _ = selected[0].placement.bounds()
        self.updating_ui = True
        for spin, value in ((self.pos_x, x0), (self.pos_y, y0), (self.angle, selected[0].placement.angle)):
            if not spin.hasFocus():
                spin.setValue(value)
        self.updating_ui = False

    def size_edited(self):
        """New width or height for a rectangle. Its first corner stays put, also when rotated."""
        if self.updating_ui:
            return
        selected = self.selected_parts()
        if len(selected) != 1:
            return
        part = selected[0]
        rectangle = rectangle_of(part.placement.program)
        if rectangle is None:
            return
        left, bottom, width, height = rectangle
        new_width, new_height = self.size_w.value(), self.size_h.value()
        if abs(new_width - width) < 1e-9 and abs(new_height - height) < 1e-9:
            return
        old = part.placement
        program = rectangle_program(left, bottom, left + new_width, bottom + new_height)
        corner = old.map_point((left, bottom))
        moved = Placement(program, 0.0, 0.0, old.angle).map_point((left, bottom))
        placement = Placement(program, corner[0] - moved[0], corner[1] - moved[1], old.angle)
        index = self.parts.index(part)
        # Without a selection for a moment the size fields would hide and lose the keyboard focus.
        self.updating_ui = True
        try:
            self.remove_part(part)
            new_part = self.add_part(placement)
            self.parts.remove(new_part)
            self.parts.insert(index, new_part)  # keep its place in the cutting order
        finally:
            self.updating_ui = False
        self.after_change([new_part])

    def placement_edited(self):
        if self.updating_ui:
            return
        selected = self.selected_parts()
        if len(selected) != 1:
            return
        part = selected[0]
        part.set_angle(self.angle.value())
        x0, y0, _, _ = part.placement.bounds()
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

    def check_collisions(self):
        regions =[(part, part.sceneBoundingRect(), part.scene_region()) for part in self.parts]
        overlapping = set()
        for i, (part_a, rect_a, region_a) in enumerate(regions):
            for part_b, rect_b, region_b in regions[i + 1:]:
                if rect_a.intersects(rect_b) and region_a.intersects(region_b):
                    overlapping.add(part_a)
                    overlapping.add(part_b)
        outside = 0
        for part in self.parts:
            if not part.on_sheet(self.sheet):
                part.set_state("outside")
                outside += 1
            elif part in overlapping:
                part.set_state("overlap")
            else:
                part.set_state("ok")
        self.outside_count, self.overlap_count = outside, len(overlapping)
        messages = [f"{len(self.parts)} figurer"]
        if outside:
            messages.append(f"<b style='color:{COLOR_OUTSIDE.name()}'>{outside} utanför plåten</b>")
        if overlapping:
            messages.append(f"<b style='color:{COLOR_OVERLAP.name()}'>{len(overlapping)} överlappar</b>")
        self.status_label.setText(" · ".join(messages))

    def update_travel(self):
        path = QPainterPath()
        if self.show_rapids.isChecked():
            previous = None
            for part in self.parts:
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
            item = QListWidgetItem(f"{row}.  {name}  (kopia {copy_numbers[name]})")
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
        paths, _ = QFileDialog.getOpenFileNames(self, "Läs in G-kod", "",
                                                "G-kod (*.ngc *.nc *.tap *.gcode *.txt);;Alla filer (*)")
        self.load_paths(paths)

    def load_paths(self, paths):
        added = []
        for path in paths:
            try:
                program = load_program(path)
            except (GCodeError, OSError) as error:
                QMessageBox.critical(self, "Kan inte läsa in filen", f"{os.path.basename(path)}\n\n{error}")
                continue
            if program.warnings:
                QMessageBox.warning(self, "Att känna till om filen",
                                    os.path.basename(path) + "\n\n" + "\n".join(program.warnings))
            placement = Placement(program)
            self.place_free(placement)
            added.append(self.add_part(placement))
        if added:
            self.after_change(added)

    def load_conversational(self, path=None):
        """Add the shapes accepted in QtPlasmaC's CONVERSATIONAL tab as one figure."""
        path = path or CONVERSATIONAL_FILE
        title = "Hämta från CONVERSATIONAL"
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            QMessageBox.information(self, title, "Det finns inget från CONVERSATIONAL. Skapa en figur där "
                                                 "och godkänn den med ADD eller ACCEPT.")
            return None
        if ";qtplasmac filtered g-code file" in text.lower():
            QMessageBox.information(self, title, "CONVERSATIONAL innehåller den fil som var inläst i MAIN, "
                                                 "inte en ny figur. Tryck NEW i CONVERSATIONAL och skapa "
                                                 "figuren där.")
            return None
        name = "CONVERSATIONAL " + time.strftime("%H:%M:%S")
        try:
            program = Program(name, text)
        except GCodeError as error:
            message = str(error)
            if "inga XY-rörelser" in message or "inga skärande" in message:
                message = "CONVERSATIONAL är tom. Skapa en figur där och godkänn den med ADD eller ACCEPT."
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
                placement = Placement(source.program, source.x, source.y, source.angle)
                self.place_free(placement)
                added.append(self.add_part(placement))
        self.after_change(added)

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
        drawing = self.view.drawing is not None
        self.sheet_width.setValue(sheet.width)
        self.sheet_height.setValue(sheet.height)
        self.sheet_width.setEnabled(sheet.is_rectangle and not drawing)
        self.sheet_height.setEnabled(sheet.is_rectangle and not drawing)
        self.draw_button.setText("Avbryt" if drawing else "Rita")
        self.corners_button.setEnabled(not drawing)
        self.machine_button.setEnabled(not drawing)
        self.rectangle_button.setEnabled(not sheet.is_rectangle and not drawing)
        if drawing:
            self.sheet_info.setText("Klicka ut hörnen. Avsluta med dubbelklick, Enter eller klick på "
                                    "första hörnet. Backsteg tar bort senaste hörnet, Esc avbryter. "
                                    "Shift ger raka kanter.")
        elif sheet.is_rectangle:
            self.sheet_info.setText("")
        else:
            self.sheet_info.setText(f"Polygon med {len(sheet.corners)} hörn, {sheet.area / 1e6:.2f} m². "
                                    "Bredd och höjd visar dess yttermått.")

    def toggle_rectangle(self):
        if self.view.shape_mode is not None:
            self.view.stop_shape()
        else:
            self.view.start_rectangle()
            self.update_sheet_panel()
        self.update_shape_panel()

    def update_shape_panel(self):
        self.rectangle_shape_button.setText("Avbryt rektangel" if self.view.shape_mode else "Rita rektangel")

    def add_shape(self, program):
        """A figure drawn in PlasmaNest, placed where it was drawn."""
        placement = Placement(program, *program.center)
        part = self.add_part(placement)
        self.after_change([part])
        self.statusBar().showMessage(f"{program.name} mm skapad. Skriv exakt bredd och höjd i panelen om "
                                     "det behövs.", 10000)
        # Ready for exact numbers: type the width, Tab, the height, Enter.
        self.size_w.setFocus()
        self.size_w.selectAll()
        return part

    def toggle_drawing(self):
        if self.view.shape_mode is not None:
            self.view.stop_shape()
        if self.view.drawing is not None:
            self.view.stop_drawing()
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
                       round(p.placement.angle, 6)) for p in self.parts))

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
        sheet, figures = state
        self.view.stop_drawing()
        self.sheet_item.set_sheet(sheet)
        self.update_sheet_panel()
        for part in list(self.parts):
            self.remove_part(part)
        for program, x, y, angle in figures:
            self.add_part(Placement(program, x, y, angle))
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
        name = os.path.basename(self.project_path) if self.project_path else "Namnlös"
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
            problems.append(f"{self.outside_count} figurer ligger utanför plåten.")
        if self.overlap_count:
            problems.append(f"{self.overlap_count} figurer överlappar varandra.")
        warning = material_warning([p.placement for p in self.parts])
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
            placements = [p.placement for p in self.parts]
            gcode = export_gcode(placements) if placements else None
            live.write(self.live_dir, gcode, len(placements), self.live_problems(), self.autoload.isChecked())
            self.live_state = state
        except Exception as error:  # never let the export stop the user's work
            self.statusBar().showMessage(f"Kunde inte uppdatera nesten för QtPlasmaC: {error}", 15000)

    def confirm_discard(self):
        if not self.is_dirty():
            return True
        answer = QMessageBox.question(self, APP_NAME, "Spara ändringarna i projektet?",
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
        self.project_path = None
        self.rebuild_list()
        self.selection_changed()
        self.refresh()
        self.reset_history()

    def open_project(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Öppna projekt", "", "PlasmaNest-projekt (*.pnest)")
        if path:
            self.open_project_path(path)

    def open_project_path(self, path):
        try:
            sheet, placements = load_project(path)
        except (OSError, ValueError, KeyError, IndexError, TypeError, GCodeError) as error:
            QMessageBox.critical(self, "Kan inte öppna projektet", str(error))
            return
        self.view.stop_drawing()
        for part in list(self.parts):
            self.remove_part(part)
        self.sheet_item.set_sheet(sheet)
        self.update_sheet_panel()
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
        path, _ = QFileDialog.getSaveFileName(self, "Spara projekt", self.project_path or "nest.pnest",
                                              "PlasmaNest-projekt (*.pnest)")
        if not path:
            return False
        if not path.lower().endswith(".pnest"):
            path += ".pnest"
        return self.write_project(path)

    def write_project(self, path):
        try:
            save_project(path, self.sheet, [p.placement for p in self.parts])
        except OSError as error:
            QMessageBox.critical(self, "Kan inte spara", str(error))
            return False
        self.project_path = path
        self.saved_state = self.undo_stack[self.undo_index]
        self.update_title()
        return True

    def export(self):
        if not self.parts:
            QMessageBox.information(self, APP_NAME, "Det finns inga figurer att exportera.")
            return
        self.refresh()
        placements = [p.placement for p in self.parts]
        problems = []
        if self.outside_count:
            problems.append(f"{self.outside_count} figurer ligger utanför plåten.")
        if self.overlap_count:
            problems.append(f"{self.overlap_count} figurer överlappar varandra.")
        warning = material_warning(placements)
        if warning:
            problems.append(warning)
        if problems:
            answer = QMessageBox.warning(self, "Kontrollera innan export",
                                         "\n".join(problems) + "\n\nExportera ändå?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        path, _ = QFileDialog.getSaveFileName(self, "Exportera till QtPlasmaC", self.export_suggestion(),
                                              "G-kod (*.ngc)")
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
                handle.write(export_gcode([p.placement for p in self.parts]))
        except OSError as error:
            QMessageBox.critical(self, "Kan inte exportera", str(error))
            return False
        message = f"Exporterade {len(self.parts)} figurer till {path}"
        directory = self.export_dir()
        if directory is not None:
            if os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(directory):
                message += ". Öppna den i QtPlasmaC med knappen Ladda nest."
            else:
                message += f". Knappen Ladda nest i QtPlasmaC hittar bara filer i {directory}."
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
        save_project(path, window.sheet, [p.placement for p in window.parts])
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
    return app.exec_()
