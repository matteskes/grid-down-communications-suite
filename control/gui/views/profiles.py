# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Profiles view (M3c): station profile + rig / audio / mesh editing.

M3c shipped the read-only YAML pane; the post-P2 passes added the everyday
forms without a text editor: rig model / serial path / baud, station
identity (callsign / Maidenhead grid), audio (backend / rig capture / ardop
capture-playback presets), and mesh (device type/path/host, node identity,
meshtasticd daemon switch). Saves run in a SnapshotWorker like every
other core call; `core/profile.save_rig` / `save_station` / `save_audio` /
`save_mesh` validate and write atomically (never the repo template — a user
profile is created instead). Fields prefill from the snapshot only while
untouched: the window autorefreshes every 10 s and must not clobber
mid-typing.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import models
from gui.services import Services
from gui.workers import SnapshotWorker


class ProfilesView(QWidget):
    changed = Signal()

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self._busy = False
        self._worker: SnapshotWorker | None = None
        self._dirty: set[str] = set()

        rig_box = QGroupBox("Rig (radio) — saved to the station profile")
        form = QFormLayout(rig_box)
        self.model_edit = QLineEdit(self)
        self.model_edit.setPlaceholderText("numeric hamlib id, e.g. 3073 (empty = Dummy)")
        self.path_edit = QLineEdit(self)
        self.path_edit.setPlaceholderText("/dev/ttyUSB0")
        self.baud_edit = QLineEdit(self)
        self.baud_edit.setPlaceholderText("38400")
        rig_edits = (
            ("model", self.model_edit),
            ("path", self.path_edit),
            ("baud", self.baud_edit),
        )
        self.save_btn = QPushButton("Save rig", self)
        self.save_btn.clicked.connect(self._save)

        station_box = QGroupBox("Station identity — saved to the station profile")
        station_form = QFormLayout(station_box)
        self.callsign_edit = QLineEdit(self)
        self.callsign_edit.setPlaceholderText("W1AW (empty = unset)")
        self.grid_edit = QLineEdit(self)
        self.grid_edit.setPlaceholderText("Maidenhead square, e.g. FN31pr")
        station_edits = (
            ("callsign", self.callsign_edit),
            ("grid", self.grid_edit),
        )
        self.station_save_btn = QPushButton("Save station", self)
        self.station_save_btn.clicked.connect(self._save_station)

        audio_box = QGroupBox("Audio (PipeWire) — saved to the station profile")
        audio_form = QFormLayout(audio_box)
        self.backend_edit = QLineEdit(self)
        self.backend_edit.setPlaceholderText("pipewire (only implemented backend)")
        self.rig_capture_edit = QLineEdit(self)
        self.rig_capture_edit.setPlaceholderText(
            "physical rig source, e.g. alsa_input.usb-codex (empty = loopback pending)"
        )
        self.ardop_capture_edit = QLineEdit(self)
        self.ardop_capture_edit.setPlaceholderText("empty = default grid_down_hf_out.monitor")
        self.ardop_playback_edit = QLineEdit(self)
        self.ardop_playback_edit.setPlaceholderText("empty = default grid_down_hf_in")
        audio_edits = (
            ("backend", self.backend_edit),
            ("rig_capture", self.rig_capture_edit),
            ("ardop_capture", self.ardop_capture_edit),
            ("ardop_playback", self.ardop_playback_edit),
        )
        self.audio_save_btn = QPushButton("Save audio", self)
        self.audio_save_btn.clicked.connect(self._save_audio)

        mesh_box = QGroupBox("Mesh (Meshtastic) — saved to the station profile")
        mesh_form = QFormLayout(mesh_box)
        self.type_combo = QComboBox(self)
        self.type_combo.addItems(("serial", "tcp", "ble"))
        self.dev_path_edit = QLineEdit(self)
        self.dev_path_edit.setPlaceholderText("/dev/ttyUSB1 (required for serial)")
        self.dev_host_edit = QLineEdit(self)
        self.dev_host_edit.setPlaceholderText("IP / hostname (required for tcp, empty = null)")
        self.long_name_edit = QLineEdit(self)
        self.long_name_edit.setPlaceholderText("node long name, e.g. W1AW Mesh")
        self.short_name_edit = QLineEdit(self)
        self.short_name_edit.setPlaceholderText("max 4 chars, e.g. W1AW")
        self.region_edit = QLineEdit(self)
        self.region_edit.setPlaceholderText("e.g. US, EU_433")
        self.preset_edit = QLineEdit(self)
        self.preset_edit.setPlaceholderText("e.g. LONG_FAST")
        self.channel_url_edit = QLineEdit(self)
        self.channel_url_edit.setPlaceholderText("channel URL — may carry the PSK; stays local")
        self.daemon_check = QCheckBox("meshtasticd daemon enabled (LoRa HAT)", self)
        mesh_edits = (
            ("dev_path", self.dev_path_edit),
            ("dev_host", self.dev_host_edit),
            ("long_name", self.long_name_edit),
            ("short_name", self.short_name_edit),
            ("region", self.region_edit),
            ("modem_preset", self.preset_edit),
            ("channel_url", self.channel_url_edit),
        )
        self.mesh_save_btn = QPushButton("Save mesh", self)
        self.mesh_save_btn.clicked.connect(self._save_mesh)

        edits = self._edits = rig_edits + station_edits + audio_edits + mesh_edits
        for name, edit in edits:
            edit.textEdited.connect(lambda _=None, n=name: self._dirty.add(n))
        # combo/checkbox change-signals also fire on programmatic sets, so
        # dirty is tracked via the user-only activated/clicked signals and
        # prefill stays clobber-free (mirrored in _prefill below)
        self.type_combo.activated.connect(lambda _=None: self._dirty.add("dev_type"))
        self.daemon_check.clicked.connect(lambda _=None: self._dirty.add("daemon_enabled"))
        for name, edit in rig_edits:
            form.addRow(name.capitalize() + ":", edit)
        form.addRow(self.save_btn)
        for name, edit in station_edits:
            station_form.addRow(name.capitalize() + ":", edit)
        station_form.addRow(self.station_save_btn)
        audio_rows = (
            ("Backend:", self.backend_edit),
            ("Rig capture:", self.rig_capture_edit),
            ("Ardop capture:", self.ardop_capture_edit),
            ("Ardop playback:", self.ardop_playback_edit),
        )
        for label, edit in audio_rows:
            audio_form.addRow(label, edit)
        audio_form.addRow(self.audio_save_btn)
        mesh_rows = (
            ("Device type:", self.type_combo),
            ("Device path:", self.dev_path_edit),
            ("Host:", self.dev_host_edit),
            ("Long name:", self.long_name_edit),
            ("Short name:", self.short_name_edit),
            ("Region:", self.region_edit),
            ("Modem preset:", self.preset_edit),
            ("Channel URL:", self.channel_url_edit),
        )
        for label, widget in mesh_rows:
            mesh_form.addRow(label, widget)
        mesh_form.addRow(self.daemon_check)
        mesh_form.addRow(self.mesh_save_btn)
        self.save_status = QLabel("", self)
        self.save_status.setWordWrap(True)

        self.path_label = QLabel("—")
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))

        lay = QVBoxLayout(self)
        lay.addWidget(rig_box)
        lay.addWidget(station_box)
        lay.addWidget(audio_box)
        lay.addWidget(mesh_box)
        lay.addWidget(self.save_status)
        lay.addWidget(self.path_label)
        lay.addWidget(self.text)

    # ------------------------------------------------------------- display
    def set_snapshot(self, snap: dict) -> None:
        prof = snap.get("profile") or {}
        self.path_label.setText(
            f"station profile: {prof.get('path', '?')}"
            f"  (station: {prof.get('id') or '(unset)'})"
        )
        self.text.setPlainText(models.profile_text(snap))
        self._prefill(snap)

    def _prefill(self, snap: dict) -> None:
        """Dirty-until-confirmed: untouched fields prefill from the snapshot;
        edited fields are protected until the snapshot matches what's typed
        (i.e. the save round-tripped) — autorefresh never clobbers typing."""
        rig = snap.get("rig") or {}
        head = snap.get("profile") or {}
        raw = snap.get("profile_raw") or {}
        audio_sec = raw.get("audio") or {}
        ardop = (audio_sec.get("presets") or {}).get("ardop") or {}
        mesh = raw.get("mesh") or {}
        device = mesh.get("device") or {}
        node = mesh.get("node") or {}
        daemon = mesh.get("daemon") or {}
        values = (
            str(rig.get("model") or ""),
            str(rig.get("path") or ""),
            str(rig.get("baud") or ""),
            str(head.get("callsign") or ""),
            str(head.get("grid") or ""),
            str(audio_sec.get("backend") or ""),
            str(audio_sec.get("rig_capture") or ""),
            str(ardop.get("capture") or ""),
            str(ardop.get("playback") or ""),
            str(device.get("path") or ""),
            str(device.get("host") or ""),  # host: null -> empty string
            str(node.get("long_name") or ""),
            str(node.get("short_name") or ""),
            str(node.get("region") or ""),
            str(node.get("modem_preset") or ""),
            str(node.get("channel_url") or ""),
        )
        for (name, edit), value in zip(self._edits, values):
            if edit.text() == value:
                self._dirty.discard(name)  # snapshot confirms the typed value
            elif name not in self._dirty:
                edit.setText(value)
        # the combo/checkbox pair sits outside the zip: same confirm-or-set
        # logic, but via currentText()/isChecked() instead of text()
        type_value = str(device.get("type") or "serial")
        known = [self.type_combo.itemText(i) for i in range(self.type_combo.count())]
        if self.type_combo.currentText() == type_value:
            self._dirty.discard("dev_type")
        elif "dev_type" not in self._dirty and type_value in known:
            self.type_combo.setCurrentText(type_value)
        daemon_value = bool(daemon.get("enabled"))
        if self.daemon_check.isChecked() == daemon_value:
            self._dirty.discard("daemon_enabled")
        elif "daemon_enabled" not in self._dirty:
            self.daemon_check.setChecked(daemon_value)

    # -------------------------------------------------------------- action
    def _save(self) -> None:
        if self._busy:
            return
        model, path, baud = (
            self.model_edit.text().strip(),
            self.path_edit.text().strip(),
            self.baud_edit.text().strip(),
        )
        self._busy = True
        self.save_status.setText("saving rig…")
        svc = self.services
        worker = SnapshotWorker(lambda: svc.rig_save(model, path, baud or None), parent=self)
        worker.done.connect(self._save_done)
        worker.failed.connect(self._save_failed)
        self._worker = worker
        worker.start()

    def _save_done(self, out: object) -> None:
        self._busy = False
        data = out or {}
        where = data.get("path", "?")
        verb = "created" if data.get("created") else "updated"
        self.save_status.setText(
            f"rig {verb}: {where} — applies on the next rig start; re-run"
            " `gd units install` if units are installed (hamlib ExecStart re-renders)."
        )
        self.changed.emit()

    def _save_failed(self, message: str) -> None:
        self._busy = False
        self.save_status.setText(f"FAILED: {message}")
        self.changed.emit()

    def _save_station(self) -> None:
        if self._busy:
            return
        callsign = self.callsign_edit.text().strip()
        grid = self.grid_edit.text().strip()
        self._busy = True
        self.save_status.setText("saving station identity…")
        svc = self.services
        worker = SnapshotWorker(lambda: svc.station_save(callsign, grid), parent=self)
        worker.done.connect(self._station_save_done)
        worker.failed.connect(self._save_failed)
        self._worker = worker
        worker.start()

    def _station_save_done(self, out: object) -> None:
        self._busy = False
        data = out or {}
        where = data.get("path", "?")
        verb = "created" if data.get("created") else "updated"
        prof = data.get("profile") or {}
        call = str(prof.get("callsign") or "(unset)")
        square = str(prof.get("grid") or "(unset)")
        self.save_status.setText(
            f"station identity {verb}: {where} — callsign={call} grid={square};"
            " the Doctor profile check flips to [ok]."
        )
        self.changed.emit()

    def _save_audio(self) -> None:
        if self._busy:
            return
        backend = self.backend_edit.text().strip()
        rig_capture = self.rig_capture_edit.text().strip()
        ardop_capture = self.ardop_capture_edit.text().strip()
        ardop_playback = self.ardop_playback_edit.text().strip()
        self._busy = True
        self.save_status.setText("saving audio…")
        svc = self.services
        worker = SnapshotWorker(
            lambda: svc.audio_save(backend, rig_capture, ardop_capture, ardop_playback),
            parent=self,
        )
        worker.done.connect(self._audio_save_done)
        worker.failed.connect(self._save_failed)
        self._worker = worker
        worker.start()

    def _audio_save_done(self, out: object) -> None:
        self._busy = False
        data = out or {}
        where = data.get("path", "?")
        verb = "created" if data.get("created") else "updated"
        self.save_status.setText(
            f"audio {verb}: {where} — presets apply on the next app (re)start;"
            " `gd audio plan` reflects rig_capture."
        )
        self.changed.emit()

    def _save_mesh(self) -> None:
        if self._busy:
            return
        dev_type, dev_path, dev_host = (
            self.type_combo.currentText().strip(),
            self.dev_path_edit.text().strip(),
            self.dev_host_edit.text().strip(),
        )
        long_name, short_name = (
            self.long_name_edit.text().strip(),
            self.short_name_edit.text().strip(),
        )
        region, modem_preset, channel_url = (
            self.region_edit.text().strip(),
            self.preset_edit.text().strip(),
            self.channel_url_edit.text().strip(),
        )
        daemon_enabled = "true" if self.daemon_check.isChecked() else "false"
        self._busy = True
        self.save_status.setText("saving mesh…")
        svc = self.services
        worker = SnapshotWorker(
            lambda: svc.mesh_save(
                dev_type,
                dev_path,
                dev_host,
                long_name,
                short_name,
                region,
                modem_preset,
                channel_url,
                daemon_enabled,
            ),
            parent=self,
        )
        worker.done.connect(self._mesh_save_done)
        worker.failed.connect(self._save_failed)
        self._worker = worker
        worker.start()

    def _mesh_save_done(self, out: object) -> None:
        self._busy = False
        data = out or {}
        where = data.get("path", "?")
        verb = "created" if data.get("created") else "updated"
        self.save_status.setText(
            f"mesh {verb}: {where} — node config applies via `meshtastic"
            " configure` (Phase 4 adapter); daemon.enabled re-renders the"
            " meshtasticd unit on `gd units install`."
        )
        self.changed.emit()
