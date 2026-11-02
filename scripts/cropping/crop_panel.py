"""The napari "Crop" dock panel: turn the box drawn in the preview into a written crop.

Loaded by 11_preview_crop.open_viewer (which owns the box) and launched with
``12_crop_roi.py --gui``. Everything the command-line cropper asks for is a widget here;
the writing itself is 12_crop_roi.write_crop, run on a background thread so the viewer
stays responsive.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import threading
import time

from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout,
                            QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                            QProgressBar, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout,
                            QWidget)

HERE = pathlib.Path(__file__).resolve().parent
RECENT_FILE = pathlib.Path(os.environ.get("CANMAC_RECENT_FILE",
                                          pathlib.Path.home() / ".config" / "macro_candi"
                                          / "recent_stores.json"))
RECENT_MAX = 8


def recent() -> list:
    """Recently opened stores that still exist, newest first (never raises)."""
    try:
        items = json.loads(RECENT_FILE.read_text())
        return [p for p in items if isinstance(p, str) and os.path.isdir(p)][:RECENT_MAX]
    except (OSError, ValueError):
        return []


def remember(store: str) -> None:
    """Put ``store`` at the top of the recent list (best effort: a read-only home is fine)."""
    try:
        items = [store] + [p for p in recent() if p != store]
        RECENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RECENT_FILE.write_text(json.dumps(items[:RECENT_MAX], indent=1))
    except OSError:
        pass


class DatasetPanel(QWidget):
    """Choose the OME-Zarr to crop from (Browse… / paste a path / Recent), then switch the
    timepoint or preview level without restarting. ``load_fn(path, t, level)`` does the
    work and raises with a user-facing message when the path is not a usable dataset."""

    def __init__(self, viewer, load_fn, t: int = 0, level=None):
        super().__init__()
        self.v, self.load_fn = viewer, load_fn
        self.store = None
        self._t0, self._level0 = t, level

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignTop)
        self.intro = QLabel(
            "<b>Choose the dataset to crop.</b> Click <b>Browse…</b> and pick its OME-Zarr "
            "folder: the folder ending in <code>.zarr</code>, e.g. <code>raw.zarr</code>. "
            "A folder inside it, or the folder just above it, works too.")
        self.intro.setWordWrap(True)
        lay.addWidget(self.intro)

        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText("/path/to/raw.zarr  (or paste a path, then Enter)")
        self.path.returnPressed.connect(lambda: self.open_path(self.path.text()))
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self.browse)
        self.open_btn = QPushButton("Open")
        self.open_btn.clicked.connect(lambda: self.open_path(self.path.text()))
        row.addWidget(self.path, 1)
        row.addWidget(self.browse_btn)
        row.addWidget(self.open_btn)
        lay.addLayout(row)

        self.recent_box = QComboBox()
        # long paths must not widen the dock (and shrink the image): elide them instead
        self.recent_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.recent_box.setMinimumContentsLength(24)
        self.recent_box.view().setTextElideMode(Qt.ElideMiddle)
        self.recent_box.activated.connect(self._recent_chosen)
        lay.addWidget(self.recent_box)
        self._fill_recent()

        self.info = QLabel("")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        row = QHBoxLayout()
        row.addWidget(QLabel("Timepoint"))
        self.t = QSpinBox()
        self.t.setToolTip("Which timepoint to look at (the Crop panel's time range starts here)")
        row.addWidget(self.t)
        row.addWidget(QLabel("Preview level"))
        self.level = QSpinBox()
        self.level.setToolTip("Pyramid level to display: higher = coarser and faster. "
                              "Crops are always written at full resolution.")
        row.addWidget(self.level)
        self.show_btn = QPushButton("Show")
        self.show_btn.setToolTip("Reload at this timepoint / level (the crop box is kept)")
        self.show_btn.clicked.connect(
            lambda: self.open_path(self.store, t=self.t.value(), level=self.level.value()))
        row.addWidget(self.show_btn)
        lay.addLayout(row)
        self.view_row = [self.t, self.level, self.show_btn]

        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.msg)
        _fluid(self.intro, self.info, self.msg)
        self._set_loaded(False)

    # ---------------- state ----------------
    def _set_loaded(self, loaded: bool):
        for w in self.view_row:
            w.setEnabled(loaded)
        self.intro.setVisible(not loaded)

    def _fill_recent(self):
        self.recent_box.clear()
        items = recent()
        self.recent_box.addItem("Recent datasets…" if items else "Recent datasets: none yet")
        for p in items:
            self.recent_box.addItem(p)
        self.recent_box.setEnabled(bool(items))

    def _recent_chosen(self, index: int):
        if index > 0:
            self.open_path(self.recent_box.itemText(index))
        self.recent_box.setCurrentIndex(0)

    def waiting(self):
        """Nothing loaded yet: say so on the (empty) canvas too."""
        self.v.text_overlay.visible = True
        self.v.text_overlay.position = "top_left"
        self.v.text_overlay.font_size = 12
        self.v.text_overlay.text = ("No dataset open yet.\n"
                                    "Choose one in the Dataset panel on the right: Browse…")

    # ---------------- actions ----------------
    def browse(self):
        start = self.path.text().strip() or self.store or str(pathlib.Path.cwd())
        start = start if os.path.isdir(start) else os.path.dirname(start) or str(pathlib.Path.cwd())
        d = QFileDialog.getExistingDirectory(
            self, "Choose the OME-Zarr folder (the one ending in .zarr)", start)
        if d:
            self.open_path(d)

    def open_path(self, path, t=None, level=None) -> bool:
        """Load ``path`` (any folder in or just above a store). Errors are shown, not raised."""
        if not path:
            self.msg.setText("<span style='color:#d33'>No folder given: click Browse… or "
                             "paste the path of the .zarr folder.</span>")
            return False
        if t is None and self.store is None:
            t, level = self._t0, self._level0  # the command line's choice, first time only
        self.msg.setText("Opening…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            prep = self.load_fn(path, t, level)
        except Exception as e:  # noqa: BLE001 - show every failure, keep the GUI alive
            name = type(e).__name__
            text = str(e) if name == "LoadError" else f"{name}: {e}"
            self.msg.setText(f"<span style='color:#d33'><b>Couldn't open it.</b> {_breakable(text)}</span>")
            print(f"couldn't open {path}: {text}")
            return False
        finally:
            QApplication.restoreOverrideCursor()
        self.store = prep["store"]
        self.path.setText(self.store)
        s0, um, ax = prep["shape0"], prep["um"], prep["axes"]
        dims = " × ".join(str(s0[ax.index(d)]) for d in "zyx")
        n_lv = len(prep["levels"])
        self.info.setText(
            f"<b>{pathlib.Path(self.store).name}</b>: {dims} voxels (z, y, x) at "
            f"{um['x']:g} µm · {prep['n_t']} timepoint{'s' if prep['n_t'] != 1 else ''} · "
            f"{len(prep['chans'])} channel{'s' if len(prep['chans']) != 1 else ''} · "
            f"{n_lv} pyramid level{'s' if n_lv != 1 else ''}")
        self.t.setRange(0, prep["n_t"] - 1)
        self.t.setValue(prep["t"])
        self.level.setRange(0, n_lv - 1)
        self.level.setValue(prep["level"])
        self._set_loaded(True)
        self._fill_recent()
        self.msg.setText("")
        return True


def _load_cropper():
    spec = importlib.util.spec_from_file_location("_crop_roi", HERE / "12_crop_roi.py")
    mod = importlib.util.module_from_spec(spec)
    cwd = os.getcwd()
    spec.loader.exec_module(mod)
    os.chdir(cwd)
    return mod


def _fluid(*labels) -> None:
    """Wrap these labels to the panel's width instead of widening the panel to fit them."""
    for lab in labels:
        lab.setWordWrap(True)
        pol = lab.sizePolicy()  # keeps word wrap's height-for-width flag
        pol.setHorizontalPolicy(QSizePolicy.Ignored)
        lab.setSizePolicy(pol)


def _breakable(text) -> str:
    """HTML-escape ``text`` and let paths in it wrap after each '/', so one long unbroken
    path can't make the panel wider than its dock."""
    import html
    return html.escape(str(text)).replace("/", "/​")


def _size(gib: float) -> str:
    return f"{gib:.2f} GiB" if gib >= 0.1 else f"{gib * 1024:.1f} MiB"


class CropPanel(QWidget):
    """Box (from the drawn shape / J-K / these spin boxes), Z range, time range, channels,
    output path, live Blender size check, Copy command, Crop with a progress bar."""

    def __init__(self, viewer, ctrl, ctx: dict):
        super().__init__()
        self.v, self.ctrl, self.ctx = viewer, ctrl, ctx
        self.crop = _load_cropper()
        self.extent = ctx["extent"]
        self._sync = False          # True while WE update spin boxes (ignore their signals)
        self._out_auto = True       # output path follows the box until the user edits it
        self._job = None            # {"frac", "msg", "done", "error", "out", "t0"} of a running crop
        self.confirm = self._ask    # replaced in tests

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignTop)

        steps = QLabel(
            "<b>1.</b> Click <b>Draw box</b>, then drag a rectangle on the image "
            "(any shape works: its bounding box is used). Or press <b>J</b> / <b>K</b> "
            "over opposite corners.<br>"
            "<b>2.</b> Scroll Z and click <b>Start here</b> / <b>End here</b>. The box is "
            "yellow on slices inside the Z range, grey outside.<br>"
            "<b>3.</b> Choose time, channels and output, then <b>Crop</b>.")
        steps.setWordWrap(True)
        lay.addWidget(steps)

        row = QHBoxLayout()
        self.draw_btn = QPushButton("Draw box")
        self.draw_btn.setToolTip("Select the 'crop box' layer in rectangle mode; drag on the image")
        self.draw_btn.clicked.connect(self.start_drawing)
        self.whole_btn = QPushButton("Whole slice")
        self.whole_btn.setToolTip("Box = the full X/Y extent (keep the Z range)")
        self.whole_btn.clicked.connect(self.whole_slice)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(lambda: self.ctrl.set_box(None, "panel"))
        for w in (self.draw_btn, self.whole_btn, self.clear_btn):
            row.addWidget(w)
        lay.addLayout(row)

        # ---- box (level-0 voxels) ----
        g = QGroupBox("Box (full-resolution voxels; end not included)")
        grid = QGridLayout(g)
        self.spin = {}
        for r, d in enumerate("xyz"):
            grid.addWidget(QLabel(f"<b>{d}</b>"), r, 0)
            for c, e in enumerate("01"):
                sb = QSpinBox()
                sb.setRange(0, self.extent[d])
                sb.setToolTip(f"--{d}{e}")
                sb.valueChanged.connect(self._from_spins)
                grid.addWidget(sb, r, 1 + c)
                self.spin[f"{d}{e}"] = sb
        self.z_start = QPushButton("Start here")
        self.z_start.setToolTip("Z start = the slice you are looking at")
        self.z_start.clicked.connect(lambda: self._z_here(0))
        self.z_end = QPushButton("End here")
        self.z_end.setToolTip("Z end = the slice you are looking at (included)")
        self.z_end.clicked.connect(lambda: self._z_here(1))
        grid.addWidget(self.z_start, 3, 1)
        grid.addWidget(self.z_end, 3, 2)
        self.box_um = QLabel("")
        grid.addWidget(self.box_um, 4, 0, 1, 3)
        lay.addWidget(g)

        # ---- time ----
        g = QGroupBox("Time")
        grid = QGridLayout(g)
        n_t = ctx["n_t"]
        self.t0 = QSpinBox()
        self.t0.setRange(0, n_t - 1)
        self.t0.setValue(ctx["t"])
        self.t1 = QSpinBox()
        self.t1.setRange(1, n_t)
        self.t1.setValue(ctx["t"] + 1)
        self.t0.valueChanged.connect(self._t_changed)
        self.t1.valueChanged.connect(self._t_changed)
        grid.addWidget(QLabel("from"), 0, 0)
        grid.addWidget(self.t0, 0, 1)
        grid.addWidget(QLabel("to (not incl.)"), 0, 2)
        grid.addWidget(self.t1, 0, 3)
        self.all_t = QPushButton(f"All {n_t}")
        self.all_t.clicked.connect(lambda: (self.t0.setValue(0), self.t1.setValue(n_t)))
        grid.addWidget(self.all_t, 1, 3)
        self.t_label = QLabel("")
        grid.addWidget(self.t_label, 1, 0, 1, 3)
        lay.addWidget(g)

        # ---- channels ----
        g = QGroupBox("Channels")
        cl = QVBoxLayout(g)
        self.chan_boxes = []
        for i, label, color in ctx["chans"]:
            cb = QCheckBox(f"{label}  ({color or 'no colour'})")
            cb.setChecked(True)
            cb.stateChanged.connect(self.update_estimate)
            cl.addWidget(cb)
            self.chan_boxes.append((cb, (i, label, color)))
        lay.addWidget(g)

        # ---- output ----
        g = QGroupBox("Output")
        grid = QGridLayout(g)
        self.out = QLineEdit()
        self.out.textEdited.connect(self._out_edited)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        grid.addWidget(self.out, 0, 0, 1, 2)
        grid.addWidget(browse, 0, 2)
        grid.addWidget(QLabel("extra 2× levels"), 1, 0)
        self.extra = QSpinBox()
        self.extra.setRange(0, 4)
        self.extra.setValue(2)
        self.extra.setToolTip("Isotropic downsampled copies for quick drafts in Blender")
        grid.addWidget(self.extra, 1, 1)
        lay.addWidget(g)

        # ---- size check + actions ----
        self.size = QLabel("")
        self.size.setWordWrap(True)
        self.size.setTextFormat(Qt.RichText)
        lay.addWidget(self.size)
        row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy command")
        self.copy_btn.setToolTip("The equivalent 12_crop_roi.py command (for batch jobs)")
        self.copy_btn.clicked.connect(self.copy_command)
        self.crop_btn = QPushButton("Crop")
        self.crop_btn.setStyleSheet("font-weight: bold;")
        self.crop_btn.clicked.connect(self.start_crop)
        row.addWidget(self.copy_btn)
        row.addWidget(self.crop_btn)
        lay.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.status)
        _fluid(steps, self.box_um, self.size, self.status)

        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._poll)

        ctrl.listeners.append(self._from_ctrl)
        self._from_ctrl(ctrl.box, "init")
        self._t_changed()

    # ---------------- box <-> widgets ----------------
    def start_drawing(self):
        layer = self.ctrl.layer
        if self.v.dims.ndisplay != 2:
            self.v.dims.ndisplay = 2
        self.v.layers.selection.active = layer
        layer.mode = "add_rectangle"
        self.status.setText("Drag a rectangle on the image. To pan instead, select a channel "
                            "layer in the layer list.")

    def whole_slice(self):
        b = self.ctrl.box
        z = b["z"] if b else (0, self.extent["z"])
        self.ctrl.set_box({"z": z, "y": (0, self.extent["y"]), "x": (0, self.extent["x"])},
                          "panel")

    def _z_here(self, which: int):
        b = self.ctrl.box or {"z": (0, self.extent["z"]), "y": (0, self.extent["y"]),
                              "x": (0, self.extent["x"])}
        z = self.ctrl.current_z()
        z0, z1 = b["z"]
        if which == 0:
            z0 = z
            z1 = max(z1, z + 1)
        else:
            z1 = z + 1
            z0 = min(z0, z)
        self.ctrl.set_box({**b, "z": (z0, z1)}, "panel")

    def _from_ctrl(self, box, source):
        if source != "spins":  # the spin boxes already show what the user typed
            if source.startswith("shape") or source == "keys":
                self.status.setText("Box set. Now scroll Z and use Start here / End here "
                                    "for the Z range." if box else "")
            self._sync = True
            try:
                for d in "xyz":
                    lo, hi = box[d] if box else (0, 0)
                    self.spin[f"{d}0"].setValue(lo)
                    self.spin[f"{d}1"].setValue(hi)
            finally:
                self._sync = False
        if self._out_auto:
            self.out.setText(self.default_out())
        self.update_estimate()

    def _from_spins(self, *_):
        if self._sync:
            return
        box = {}
        for d in "xyz":
            lo, hi = self.spin[f"{d}0"].value(), self.spin[f"{d}1"].value()
            if hi <= lo:  # keep at least one voxel while the user is typing
                return self.update_estimate()
            box[d] = (lo, hi)
        self.ctrl.set_box(box, "spins")

    def _t_changed(self, *_):
        if self.t1.value() <= self.t0.value():
            self.t1.setValue(self.t0.value() + 1)
        n = self.t1.value() - self.t0.value()
        self.t_label.setText(f"{n} timepoint{'s' if n != 1 else ''}")
        if self._out_auto:
            self.out.setText(self.default_out())
        self.update_estimate()

    def _out_edited(self, *_):
        self._out_auto = False

    # ---------------- values ----------------
    def selected_chans(self) -> list:
        return [ch for cb, ch in self.chan_boxes if cb.isChecked()]

    def channel_spec(self) -> str:
        sel = self.selected_chans()
        return "all" if len(sel) == len(self.chan_boxes) else ",".join(str(i) for i, _, _ in sel)

    def default_out(self) -> str:
        b = self.ctrl.box
        if not b:
            return "crops/roi.zarr"
        return (f"crops/roi_x{b['x'][0]}-{b['x'][1]}_y{b['y'][0]}-{b['y'][1]}"
                f"_z{b['z'][0]}-{b['z'][1]}_t{self.t0.value()}-{self.t1.value()}.zarr")

    def out_path(self) -> str:
        p = pathlib.Path(os.path.expanduser(self.out.text().strip()))
        if not p.is_absolute():
            p = pathlib.Path(self.ctx["caller_cwd"]) / p
        return str(p.resolve())

    def browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "Crop output (.zarr folder)",
                                              self.out_path(), "OME-Zarr (*.zarr)")
        if path:
            if not path.endswith(".zarr"):
                path += ".zarr"
            self.out.setText(path)
            self._out_auto = False

    def update_estimate(self, *_):
        b, um = self.ctrl.box, self.ctx["um"]
        if b is None:
            self.box_um.setText("")
            self.size.setText("<i>No box yet: draw one, or press J / K on opposite corners.</i>")
            self.crop_btn.setEnabled(False)
            self.copy_btn.setEnabled(False)
            return
        self.box_um.setText("  ".join(
            f"{d}: {b[d][1] - b[d][0]} vox = {(b[d][1] - b[d][0]) * um[d]:.1f} µm" for d in "zyx"))
        nch = len(self.selected_chans())
        nt = self.t1.value() - self.t0.value()
        est = self.crop.estimate(self.ctx["levels"], self.ctx["axes"], b, 0, max(nch, 1), nt)
        gib = est["vdb_gib"]
        if est["fits"]:
            verdict = (f"<span style='color:#2e9d4f'><b>fits</b></span> Blender's "
                       f"{self.crop.BLENDER_LIMIT_GIB:g} GiB limit")
        else:
            verdict = (f"<span style='color:#d33'><b>OVER</b> Blender's "
                       f"{self.crop.BLENDER_LIMIT_GIB:g} GiB limit by "
                       f"{gib / self.crop.BLENDER_LIMIT_GIB:.1f}×</span>: shrink the box "
                       f"(Z is often easiest) or render in Cycles")
        self.size.setText(
            f"In Blender: <b>{_size(gib)}</b> per channel per timepoint, {verdict}.<br>"
            f"On disk: {_size(est['disk_gib'])} ({nch} channel{'s' if nch != 1 else ''} × "
            f"{nt} timepoint{'s' if nt != 1 else ''}).")
        busy = self._job is not None and not self._job["done"]
        self.crop_btn.setEnabled(nch > 0 and not busy)
        self.copy_btn.setEnabled(nch > 0)

    def command(self) -> str:
        b = self.ctrl.box
        return self.crop.crop_command(self.ctx["store"], self.out_path(), b, self.t0.value(),
                                      self.t1.value(), self.channel_spec(), self.extra.value(),
                                      self.ctx.get("include_lysed", False))

    def copy_command(self):
        cmd = self.command()
        QApplication.clipboard().setText(cmd)
        print(f"\n{cmd}\n")
        self.status.setText("Command copied to the clipboard (and printed in the terminal).")

    # ---------------- cropping ----------------
    def _ask(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text,
                                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes

    def start_crop(self):
        b, chans = self.ctrl.box, self.selected_chans()
        if b is None or not chans:
            return
        out = self.out_path()
        overwrite = False
        if os.path.exists(out):
            if not self.confirm("Replace existing crop?", f"{out}\nalready exists. Replace it?"):
                return
            overwrite = True
        est = self.crop.estimate(self.ctx["levels"], self.ctx["axes"], b, 0, len(chans),
                                 self.t1.value() - self.t0.value())
        if not est["fits"] and not self.confirm(
                "Too big for Blender's viewport",
                f"Each volume would be {est['vdb_gib']:.1f} GiB, over Blender's "
                f"{self.crop.BLENDER_LIMIT_GIB:g} GiB EEVEE/viewport limit (Cycles can still "
                f"render it). Crop anyway?"):
            return

        print(f"\ncropping (same as):\n  {self.command()}\n")
        job = {"frac": 0.0, "msg": "starting…", "done": False, "error": None, "out": out,
               "t0": time.time()}
        self._job = job
        args = (self.ctx["store"], out, self.ctx["levels"], self.ctx["axes"], dict(b),
                self.t0.value(), self.t1.value(), chans)
        kw = dict(extra_levels=self.extra.value(), overwrite=overwrite,
                  progress=lambda f: job.__setitem__("frac", f),
                  log=lambda m: (job.__setitem__("msg", m.strip()), print(m, flush=True)))

        def run():
            try:
                self.crop.write_crop(*args, **kw)
            except Exception as e:  # shown in the panel, not lost in a thread
                job["error"] = f"{type(e).__name__}: {e}"
            job["done"] = True

        self.crop_btn.setEnabled(False)
        self.bar.setValue(0)
        self.bar.setVisible(True)
        self.status.setText("Cropping… the viewer stays usable meanwhile.")
        threading.Thread(target=run, daemon=True).start()
        self.timer.start()

    def _poll(self):
        job = self._job
        if job is None:
            return
        self.bar.setValue(int(job["frac"] * 1000))
        if not job["done"]:
            self.status.setText(f"Cropping… {job['frac'] * 100:.0f} %   {job['msg']}")
            return
        self.timer.stop()
        self.update_estimate()
        if job["error"]:
            self.bar.setVisible(False)
            self.status.setText(f"<span style='color:#d33'><b>Crop failed:</b> {_breakable(job['error'])}</span>")
            print(f"crop failed: {job['error']}")
            return
        secs = time.time() - job["t0"]
        target = f"{job['out']}/0"
        QApplication.clipboard().setText(target)
        self.status.setText(
            f"<b>Done</b> in {secs:.0f} s. In Blender → Microscopy Nodes, open:<br>"
            f"<code>{_breakable(target)}</code><br>(path copied to the clipboard)")
        print(f"\ndone in {secs:.1f}s -> {job['out']}\nOpen in Blender: Microscopy Nodes -> {target}")
