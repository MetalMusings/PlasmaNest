"""Qt user interface. The scene uses G-code coordinates in mm; the view flips Y."""

import math
import os
import sys

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import (QBrush, QColor, QKeySequence, QPainter, QPainterPath, QPainterPathStroker,
                         QPen, QPolygonF)
from PyQt5.QtWidgets import (QAbstractItemView, QAction, QApplication, QCheckBox, QDialog,
                             QDialogButtonBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout,
                             QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsSimpleTextItem,
                             QGraphicsView, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QMainWindow, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .gcode import GCodeError, load_program
from .nest import Placement, export_gcode, load_project, material_warning, save_project

APP_NAME = "PlasmaNest"
PART_GAP = 10.0        # mm between figures placed automatically
NUDGE_STEP = 1.0       # mm per arrow key press
NUDGE_STEP_LARGE = 10.0
HANDLE_DISTANCE = 42   # pixels from the figure to the rotation handle
ROTATE_SNAP = 15.0     # degrees with Ctrl held
ROTATE_MIN_RADIUS = 15 # pixels from the pivot; closer than this the angle is too jumpy

COLOR_CUT = QColor("#1565c0")
COLOR_SELECTED = QColor("#00a152")
COLOR_OVERLAP = QColor("#ef6c00")
COLOR_OUTSIDE = QColor("#d32f2f")
COLOR_RAPID = QColor("#7a7a7a")
COLOR_SHEET = QColor("#e4e7ea")
COLOR_SHEET_EDGE = QColor("#37474f")
COLOR_GRID = QColor("#ccd2d7")
COLOR_VIEW = QColor("#90a4ae")


def cosmetic_pen(color, width=1.0, style=Qt.SolidLine):
    pen = QPen(color, width, style)
    pen.setCosmetic(True)
    return pen


def normalize_angle(angle):
    angle = math.fmod(angle, 360.0)
    return angle + 360.0 if angle < 0 else angle


class SheetItem(QGraphicsItem):
    """The steel sheet, with its lower left corner at the G-code origin."""

    GRID = 100.0

    def __init__(self, width, height):
        super().__init__()
        self.width, self.height = width, height
        self.setZValue(-10)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def set_size(self, width, height):
        self.prepareGeometryChange()
        self.width, self.height = width, height

    def boundingRect(self):
        return QRectF(-2, -2, self.width + 4, self.height + 4)

    def paint(self, painter, option, widget=None):
        rect = QRectF(0, 0, self.width, self.height)
        painter.fillRect(rect, COLOR_SHEET)
        painter.setPen(cosmetic_pen(COLOR_GRID))
        x = self.GRID
        while x < self.width:
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height))
            x += self.GRID
        y = self.GRID
        while y < self.height:
            painter.drawLine(QPointF(0, y), QPointF(self.width, y))
            y += self.GRID
        painter.setPen(cosmetic_pen(COLOR_SHEET_EDGE, 2.0))
        painter.drawRect(rect)


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
        sheet = self.window_.sheet
        rect = QRectF(0, 0, sheet.width, sheet.height)
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
            self.scale(factor, factor)
            self.window_.update_handles()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
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
        self.window_.statusBar().showMessage(f"X {position.x():.1f}   Y {position.y():.1f} mm")
        super().mouseMoveEvent(event)

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
        super().mouseReleaseEvent(event)
        self.window_.commit()

    def keyPressEvent(self, event):
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
        self.sheet = SheetItem(3000.0, 1500.0)
        self.scene_.addItem(self.sheet)
        self.travel = QGraphicsPathItem()
        self.travel.setPen(cosmetic_pen(COLOR_RAPID, 1.0, Qt.DotLine))
        self.travel.setAcceptedMouseButtons(Qt.NoButton)
        self.travel.setZValue(-1)
        self.scene_.addItem(self.travel)
        self.view = NestView(self.scene_, self)
        self.setCentralWidget(self.view)

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
        layout.addWidget(sheet_box)

        parts_box = QGroupBox("Figurer (i skärordning)")
        parts_layout = QVBoxLayout(parts_box)
        self.part_list = QListWidget()
        self.part_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.part_list.itemSelectionChanged.connect(self.list_selection_changed)
        parts_layout.addWidget(self.part_list)
        row = QHBoxLayout()
        self.load_button = self._button("Läs in…", self.load_files, row)
        self.copy_button = self._button("Kopiera…", self.copy_selection, row)
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
        dock.setWidget(panel)
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

    def update_placement_fields(self):
        selected = self.selected_parts()
        if len(selected) != 1:
            return
        x0, y0, _, _ = selected[0].placement.bounds()
        self.updating_ui = True
        for spin, value in ((self.pos_x, x0), (self.pos_y, y0), (self.angle, selected[0].placement.angle)):
            if not spin.hasFocus():
                spin.setValue(value)
        self.updating_ui = False

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
        width, height = self.sheet.width, self.sheet.height
        regions = [(part, part.sceneBoundingRect(), part.scene_region()) for part in self.parts]
        overlapping = set()
        for i, (part_a, rect_a, region_a) in enumerate(regions):
            for part_b, rect_b, region_b in regions[i + 1:]:
                if rect_a.intersects(rect_b) and region_a.intersects(region_b):
                    overlapping.add(part_a)
                    overlapping.add(part_b)
        outside = 0
        for part in self.parts:
            if not part.placement.inside(width, height):
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
        xs = sorted({PART_GAP} | {b[2] + PART_GAP for b in boxes})
        ys = sorted({PART_GAP} | {b[3] + PART_GAP for b in boxes})
        for y in ys:
            for x in xs:
                if x + width > self.sheet.width or y + height > self.sheet.height:
                    continue
                if not any(x < b[2] + PART_GAP and x + width + PART_GAP > b[0]
                           and y < b[3] + PART_GAP and y + height + PART_GAP > b[1] for b in boxes):
                    return x, y
        right = max([self.sheet.width] + [b[2] for b in boxes]) + PART_GAP * 5
        return right, 0.0

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

    def copy_selection(self):
        selected = self.selected_parts()
        if not selected:
            return
        dialog = CopyDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.copy_parts(selected, dialog.count.value())

    def copy_parts(self, parts, count):
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

    def sheet_changed(self):
        width, height = self.sheet_width.value(), self.sheet_height.value()
        if (width, height) != (self.sheet.width, self.sheet.height):
            self.sheet.set_size(width, height)
            self.refresh()
            self.commit()
            if not self.user_has_view:
                self.view.fit()

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
        return (self.sheet.width, self.sheet.height,
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
        width, height, figures = state
        self.sheet.set_size(width, height)
        self.updating_ui = True
        self.sheet_width.setValue(width)
        self.sheet_height.setValue(height)
        self.updating_ui = False
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
            width, height, placements = load_project(path)
        except (OSError, ValueError, KeyError, IndexError, GCodeError) as error:
            QMessageBox.critical(self, "Kan inte öppna projektet", str(error))
            return
        for part in list(self.parts):
            self.remove_part(part)
        self.sheet.set_size(width, height)
        self.updating_ui = True
        self.sheet_width.setValue(width)
        self.sheet_height.setValue(height)
        self.updating_ui = False
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
            save_project(path, self.sheet.width, self.sheet.height, [p.placement for p in self.parts])
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
        suggestion = os.path.splitext(self.project_path)[0] + ".ngc" if self.project_path else "nest.ngc"
        path, _ = QFileDialog.getSaveFileName(self, "Exportera till QtPlasmaC", suggestion, "G-kod (*.ngc)")
        if path:
            self.export_to(path)

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
        self.statusBar().showMessage(f"Exporterade {len(self.parts)} figurer till {path}", 8000)
        return True

    def closeEvent(self, event):
        if self.confirm_discard():
            event.accept()
        else:
            event.ignore()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    paths = sys.argv[1:]
    projects = [p for p in paths if p.lower().endswith(".pnest")]
    if projects:
        window.open_project_path(projects[0])
    window.load_paths([p for p in paths if not p.lower().endswith(".pnest")])
    window.view.fit()
    return app.exec_()
