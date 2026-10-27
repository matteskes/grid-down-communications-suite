# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""GUI smoke tests (M3a) — PySide6 offscreen via pytest-qt (spec §7).

QT_QPA_PLATFORM=offscreen is forced before any Qt import so the suite runs
headless; the production target is Fedora 45 x86_64 (GNOME/Wayland), where
the same window code runs under the platform's Qt.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402

from gui.main_window import MainWindow  # noqa: E402
from gui.services import Services  # noqa: E402
from gui.views.bundles import BundlesView  # noqa: E402
from gui.views.catalog import CatalogView  # noqa: E402
from gui.views.dashboard import DashboardView  # noqa: E402
from gui.views.doctor import DoctorView  # noqa: E402
from gui.views.profiles import ProfilesView  # noqa: E402
from gui.workers import SnapshotWorker  # noqa: E402


def _snap() -> dict:
    return {
        "version": "0.5.0-m4",
        "profile": {
            "path": "/etc/grid-down/profiles/default.yaml",
            "id": "default",
            "callsign": "W1AW",
            "grid": "FN31pr",
        },
        "rig": {
            "argv": ["rigctld", "-T", "127.0.0.1", "-t", "4532", "-v", "-m", "1"],
            "notes": [],
            "endpoint": "127.0.0.1:4532",
            "model": "3073",
            "path": "/dev/ttyUSB0",
            "baud": "38400",
        },
        "audio": {"present": ["grid_down_hf_out"], "missing": ["grid_down_hf_in"]},
        "instances": {
            "apps": [{"id": "hamlib", "state": "running", "pid": 42}],
            "bundles": {
                "winlink": {"running": ["hamlib", "ardop"], "total": 3},
                "js8": {"running": [], "total": 2},
            },
        },
        "bundles": {
            "js8": {"description": "JS8 weak-signal messaging", "members": ["hamlib", "js8call"]},
            "packet": {
                "description": "Packet radio terminal stack",
                "members": ["direwolf", "qttermtcp", "paracon"],
            },
            "winlink": {
                "description": "HF/VHF email via Winlink",
                "members": ["hamlib", "direwolf", "ardop", "pat"],
            },
        },
        "profile_raw": {
            "profile": {"id": "default", "callsign": "W1AW", "grid": "FN31pr"},
            "rig": {"model": "", "path": "", "baud": ""},
            "audio": {
                "backend": "pipewire",
                "rig_capture": "",
                "presets": {
                    "ardop": {
                        "capture": "grid_down_hf_out.monitor",
                        "playback": "grid_down_hf_in",
                    }
                },
            },
            "mesh": {
                "device": {"type": "serial", "path": "/dev/ttyUSB1", "host": None},
                "node": {
                    "long_name": "W1AW Mesh",
                    "short_name": "W1AW",
                    "region": "US",
                    "modem_preset": "LONG_FAST",
                    "channel_url": "",
                },
                "daemon": {"enabled": False},
            },
        },
        "apps": {
            "ardop": {
                "name": "ARDOP TNC",
                "category": "winlink",
                "launch": {"via": "managed-service"},
                "ui": {"tagline": "ARDOP modem"},
            },
            "direwolf": {
                "name": "Direwolf",
                "category": "packet",
                "launch": {"via": "managed-service"},
                "ui": {},
            },
            "js8call": {"name": "JS8Call", "category": "winlink", "ui": {}},
        },
        "install": {
            "ardop": {"state": "installed", "detail": "/usr/bin/ardopc"},
            "direwolf": {"state": "missing", "detail": "direwolf not on PATH"},
        },
        "winehub": {
            "prefix": True,
            "prefix_path": "/opt/grid-down/wine-prefix",
            "wine": "/usr/bin/wine",
            "com": {"com5": "/dev/ttyUSB0"},
            "apps": [{"id": "vara-hf", "exe": "VARA.exe", "running": False, "pid": None}],
        },
        "doctor": {
            "results": [],
            "counts": {"ok": 8, "warn": 5, "fail": 0, "skip": 4},
            "all_ok": True,
        },
        "errors": {},
    }


def _fake_services() -> Services:
    snap = _snap()
    return Services(
        version=lambda: snap["version"],
        profile=lambda: snap["profile"],
        profile_raw=lambda: snap["profile_raw"],
        rig=lambda: snap["rig"],
        audio=lambda: snap["audio"],
        instances=lambda: snap["instances"],
        apps=lambda: snap["apps"],
        bundles=lambda: snap["bundles"],
        doctor=lambda: snap["doctor"]["results"],
        bundle_start=lambda name: {"bundle": name, "notes": [f"running: {name}"]},
        bundle_stop=lambda name: {"bundle": name, "notes": [f"stopped: {name}"]},
        audio_up=lambda: {
            "created": [{"endpoint": "grid_down_hf_out", "module": 7}],
            "skipped": ["grid_down_hf_in"],
        },
        audio_down=lambda: {"unloaded": ["grid_down_hf_out"]},
        rig_save=lambda m, p, b: {
            "path": "/home/u/.config/grid-down/profiles/default.yaml",
            "created": True,
            "rig": {"model": m, "path": p, "baud": b or ""},
        },
        station_save=lambda c, g: {
            "path": "/home/u/.config/grid-down/profiles/default.yaml",
            "created": True,
            "profile": {"id": "default", "callsign": c, "grid": g},
        },
        audio_save=lambda be, rc, ac, ap: {
            "path": "/home/u/.config/grid-down/profiles/default.yaml",
            "created": True,
            "audio": {
                "backend": be or "pipewire",
                "rig_capture": rc,
                "presets": {"ardop": {"capture": ac, "playback": ap}},
            },
        },
        mesh_save=lambda dt, dp, dh, ln, sn, rg, mp, cu, de: {
            "path": "/home/u/.config/grid-down/profiles/default.yaml",
            "created": True,
            "mesh": {
                "device": {"type": dt, "path": dp, "host": dh or None},
                "node": {
                    "long_name": ln,
                    "short_name": sn,
                    "region": rg,
                    "modem_preset": mp,
                    "channel_url": cu,
                },
                "daemon": {"enabled": de == "true"},
            },
        },
        install_state=lambda: snap["install"],
        winehub_status=lambda: snap["winehub"],
        winehub_start=lambda app_id: {"id": app_id, "state": "running", "pid": 4242},
        winehub_stop=lambda app_id: {"id": app_id, "state": "stopped"},
    )


def _spy_services(base: Services, **over) -> Services:
    """Copy a Services with selected callables replaced (test spy)."""
    fields = {
        f: getattr(base, f)
        for f in (
            "version",
            "profile",
            "profile_raw",
            "rig",
            "audio",
            "instances",
            "apps",
            "bundles",
            "doctor",
            "bundle_start",
            "bundle_stop",
            "audio_up",
            "audio_down",
            "rig_save",
            "station_save",
            "audio_save",
            "mesh_save",
            "install_state",
            "winehub_status",
            "winehub_start",
            "winehub_stop",
        )
    }
    fields.update(over)
    return Services(**fields)


def test_dashboard_set_snapshot_populates_labels(qtbot):
    view = DashboardView(_fake_services())
    qtbot.addWidget(view)
    with qtbot.waitSignal(view.refreshed, timeout=2000):
        view.set_snapshot(_snap())
    assert view.header_labels["Callsign"].text() == "W1AW"
    assert "endpoint: 127.0.0.1:4532" in view.rig_label.text()
    assert "grid_down_hf_in — MISSING" in view.audio_label.text()
    assert "hamlib — running" in view.instances_label.text()
    assert view.doctor_label.text() == "doctor: 8 ok, 5 warn, 0 fail, 4 skip"
    assert not view.errors_label.isVisibleTo(view)


def test_dashboard_shows_section_errors(qtbot):
    view = DashboardView(_fake_services())
    qtbot.addWidget(view)
    snap = _snap()
    snap["audio"] = {}
    snap["errors"] = {"audio": "FileNotFoundError: pactl"}
    view.set_snapshot(snap)
    assert "unavailable — FileNotFoundError: pactl" in view.audio_label.text()
    assert view.errors_label.isVisibleTo(view)
    assert "audio: FileNotFoundError: pactl" in view.errors_label.text()


def test_worker_emits_done_with_result(qtbot):
    snap = _snap()
    worker = SnapshotWorker(lambda: snap)
    with qtbot.waitSignal(worker.done, timeout=5000) as blocker:
        worker.start()
    assert blocker.args[0] is snap


def test_worker_emits_failed_on_raise(qtbot):
    def boom():
        raise RuntimeError("no pactl")

    worker = SnapshotWorker(boom)
    with qtbot.waitSignal(worker.failed, timeout=5000) as blocker:
        worker.start()
    assert "RuntimeError: no pactl" in blocker.args[0]


def test_main_window_builds_with_placeholders(qtbot):
    win = MainWindow(_fake_services(), autostart=False)
    qtbot.addWidget(win)
    assert win.windowTitle().startswith("Grid Down")
    assert win.nav.count() == 5
    win.dashboard.set_snapshot(_snap())
    assert win.dashboard.header_labels["Version"].text() == "0.5.0-m4"


def test_main_window_snapshot_fans_out_to_all_pages(qtbot):
    """P2 VM-pass regression: the live window only ever fed the Dashboard,
    leaving Bundles/Catalog/Doctor/Profile empty outside --selfcheck. The
    window's set_snapshot must reach every page."""
    win = MainWindow(_fake_services(), autostart=False)
    qtbot.addWidget(win)
    win.set_snapshot(_snap())
    assert set(win.bundles._tiles) == {"js8", "packet", "winlink"}
    assert win.catalog.model.rowCount() > 0
    assert win.doctor.summary_label.text() != "no snapshot yet"
    assert win.profiles.text.toPlainText()


def test_main_window_autostart_delivers_snapshot(qtbot):
    """The autostart worker must actually deliver: MainWindow stores services
    and start_autorefresh populates the pages (P2 VM finding — the worker
    died on a missing self.services and every page stayed empty)."""
    win = MainWindow(_fake_services(), autostart=True)
    qtbot.addWidget(win)
    qtbot.waitUntil(lambda: len(win.bundles._tiles) > 0, timeout=10_000)
    assert win.dashboard.header_labels["Version"].text() == "0.5.0-m4"


def test_bundles_tiles_counts_and_cancel(qtbot):
    view = BundlesView(_fake_services())
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    assert set(view._tiles) == {"js8", "packet", "winlink"}
    assert view._tiles["winlink"][0].text() == "2/3 running"
    assert view._tiles["js8"][0].text() == "0/2 running"
    view._confirm = lambda t, x: False
    view._stop("winlink")
    assert "cancelled: stop winlink" in view.notes.toPlainText()


def test_bundles_start_confirmed_runs_in_worker(qtbot):
    calls = []
    services = _spy_services(
        _fake_services(),
        bundle_start=lambda n: (calls.append(n), {"bundle": n, "notes": [f"running: {n}"]})[1],
    )
    view = BundlesView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    view._confirm = lambda t, x: True
    with qtbot.waitSignal(view.changed, timeout=5000):
        view._start("js8")
    assert calls == ["js8"]
    assert "running: js8" in view.notes.toPlainText()


def test_dashboard_audio_up_runs_and_refreshes(qtbot):
    calls = []
    services = _spy_services(
        _fake_services(),
        audio_up=lambda: (
            calls.append("up"),
            {"created": [{"endpoint": "e", "module": 7}], "skipped": []},
        )[1],
    )
    view = DashboardView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    with qtbot.waitSignal(view.refreshed, timeout=5000):
        view._audio_up()
    assert calls == ["up"]


def test_dashboard_audio_down_requires_confirm(qtbot):
    calls = []
    services = _spy_services(
        _fake_services(), audio_down=lambda: (calls.append("down"), {"unloaded": []})[1]
    )
    view = DashboardView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    view._confirm = lambda t, x: False
    view._audio_down()
    assert calls == []  # refused — no worker, no core call
    assert view._worker is None


def test_profiles_view_renders_yaml(qtbot):
    view = ProfilesView(_fake_services())
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    assert "station profile:" in view.path_label.text()
    text = view.text.toPlainText()
    assert "callsign: W1AW" in text and "rig:" in text


def test_catalog_lists_and_filters(qtbot):
    view = CatalogView(_fake_services())
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    assert view.model.rowCount() == 3
    assert view.proxy.rowCount() == 3
    assert view.count_label.text() == "3 apps"
    view.filter_box.setText("ardop")  # id match
    assert view.proxy.rowCount() == 1
    view.filter_box.setText("packet")  # category match
    assert view.proxy.rowCount() == 1
    view.filter_box.setText("zzz-none")
    assert view.proxy.rowCount() == 0
    assert view.count_label.text() == "3 apps (0 shown)"


def test_catalog_columns_and_state(qtbot):
    view = CatalogView(_fake_services())
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    assert view.model.data(view.model.index(0, 0)) == "ardop"  # sorted by id
    assert view.model.data(view.model.index(0, 1)) == "ARDOP TNC"
    assert view.model.data(view.model.index(0, 4)) == "installed"  # install probe (M5)
    assert view.model.data(view.model.index(0, 5)) == "-"  # not running
    assert view.model.data(view.model.index(0, 0), 3) == "ARDOP modem"  # tooltip
    assert view.model.data(view.model.index(2, 0), 3) is None  # js8call: none


def test_doctor_rows_colors_and_summary(qtbot):
    view = DoctorView(_fake_services())
    qtbot.addWidget(view)
    snap = _snap()
    snap["doctor"]["results"] = [
        {"id": "platform", "title": "platform", "status": "ok", "detail": "linux"},
        {"id": "ports", "title": "port conflicts", "status": "fail", "detail": "4532 busy"},
        {"id": "deferred", "title": "deferred", "status": "skip", "detail": "n/a"},
    ]
    view.set_snapshot(snap)
    assert view.tree.topLevelItemCount() == 3
    assert view.tree.topLevelItem(1).text(0) == "FAIL"
    assert view.tree.topLevelItem(1).foreground(0).color().name() == "#b02020"
    assert view.tree.topLevelItem(2).foreground(0).color().name() == "#6e7781"
    assert "doctor: 8 ok, 5 warn, 0 fail, 4 skip" in view.summary_label.text()


def test_doctor_copy_json(qtbot):
    view = DoctorView(_fake_services())
    qtbot.addWidget(view)
    snap = _snap()
    snap["doctor"]["results"] = [
        {"id": "ports", "title": "port conflicts", "status": "fail", "detail": "4532 busy"}
    ]
    view.set_snapshot(snap)
    checks = json.loads(view.copy_json())["checks"]
    assert checks[0]["id"] == "ports" and checks[0]["status"] == "fail"


def test_profiles_form_prefills_and_saves(qtbot):
    calls = []
    services = _spy_services(
        _fake_services(),
        rig_save=lambda m, p, b: (
            calls.append((m, p, b)),
            {
                "path": "/home/u/.config/grid-down/profiles/default.yaml",
                "created": False,
                "rig": {},
            },
        )[1],
    )
    view = ProfilesView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    assert view.model_edit.text() == "3073"
    assert view.path_edit.text() == "/dev/ttyUSB0"
    assert view.baud_edit.text() == "38400"
    qtbot.keyClicks(view.path_edit, "1")  # user edit -> dirty -> survives prefill
    with qtbot.waitSignal(view.changed, timeout=5000):
        view.save_btn.click()
    assert calls == [("3073", "/dev/ttyUSB01", "38400")]
    assert "rig updated:" in view.save_status.text()
    snap = _snap()
    snap["rig"]["path"] = "/dev/ttyUSB9"
    view.set_snapshot(snap)
    assert view.path_edit.text() == "/dev/ttyUSB01"  # dirty field untouched


def test_profiles_save_failure_surfaces(qtbot):
    def boom(m, p, b):
        raise RuntimeError("bad rig model")

    services = _spy_services(_fake_services(), rig_save=boom)
    view = ProfilesView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    with qtbot.waitSignal(view.changed, timeout=5000):
        view.save_btn.click()
    assert "FAILED" in view.save_status.text()
    assert "bad rig model" in view.save_status.text()


def test_profiles_station_form_prefills_and_saves(qtbot):
    calls = []
    services = _spy_services(
        _fake_services(),
        station_save=lambda c, g: (
            calls.append((c, g)),
            {
                "path": "/home/u/.config/grid-down/profiles/default.yaml",
                "created": False,
                "profile": {"id": "default", "callsign": c, "grid": g},
            },
        )[1],
    )
    view = ProfilesView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    assert view.callsign_edit.text() == "W1AW"
    assert view.grid_edit.text() == "FN31pr"
    qtbot.keyClicks(view.callsign_edit, "X")  # user edit -> dirty -> survives prefill
    with qtbot.waitSignal(view.changed, timeout=5000):
        view.station_save_btn.click()
    assert calls == [("W1AWX", "FN31pr")]
    assert "station identity updated:" in view.save_status.text()
    snap = _snap()
    snap["profile"]["callsign"] = "K5ABC"
    view.set_snapshot(snap)
    assert view.callsign_edit.text() == "W1AWX"  # dirty field untouched


def test_profiles_station_save_failure_surfaces(qtbot):
    def boom(c, g):
        raise RuntimeError("bad grid square")

    services = _spy_services(_fake_services(), station_save=boom)
    view = ProfilesView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    with qtbot.waitSignal(view.changed, timeout=5000):
        view.station_save_btn.click()
    assert "FAILED" in view.save_status.text()
    assert "bad grid square" in view.save_status.text()


def test_profiles_audio_form_prefills_and_saves(qtbot):
    calls = []
    services = _spy_services(
        _fake_services(),
        audio_save=lambda be, rc, ac, ap: (
            calls.append((be, rc, ac, ap)),
            {
                "path": "/home/u/.config/grid-down/profiles/default.yaml",
                "created": False,
                "audio": {
                    "backend": be or "pipewire",
                    "rig_capture": rc,
                    "presets": {"ardop": {"capture": ac, "playback": ap}},
                },
            },
        )[1],
    )
    view = ProfilesView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    assert view.backend_edit.text() == "pipewire"
    assert view.rig_capture_edit.text() == ""
    assert view.ardop_capture_edit.text() == "grid_down_hf_out.monitor"
    assert view.ardop_playback_edit.text() == "grid_down_hf_in"
    qtbot.keyClicks(view.rig_capture_edit, "alsa_input.usb-codex")
    with qtbot.waitSignal(view.changed, timeout=5000):
        view.audio_save_btn.click()
    assert calls == [
        (
            "pipewire",
            "alsa_input.usb-codex",
            "grid_down_hf_out.monitor",
            "grid_down_hf_in",
        )
    ]
    assert "audio updated:" in view.save_status.text()
    snap = _snap()
    snap["profile_raw"]["audio"]["rig_capture"] = "other_source"
    view.set_snapshot(snap)
    assert view.rig_capture_edit.text() == "alsa_input.usb-codex"  # dirty field untouched


def test_profiles_audio_save_failure_surfaces(qtbot):
    def boom(be, rc, ac, ap):
        raise RuntimeError("bad audio backend")

    services = _spy_services(_fake_services(), audio_save=boom)
    view = ProfilesView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    with qtbot.waitSignal(view.changed, timeout=5000):
        view.audio_save_btn.click()
    assert "FAILED" in view.save_status.text()
    assert "bad audio backend" in view.save_status.text()


def test_profiles_mesh_form_prefills_and_saves(qtbot):
    calls = []
    services = _spy_services(
        _fake_services(),
        mesh_save=lambda dt, dp, dh, ln, sn, rg, mp, cu, de: (
            calls.append((dt, dp, dh, ln, sn, rg, mp, cu, de)),
            {
                "path": "/home/u/.config/grid-down/profiles/default.yaml",
                "created": False,
                "mesh": {"device": {"type": dt, "path": dp, "host": dh or None}},
            },
        )[1],
    )
    view = ProfilesView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    # combo/checkbox prefill from the snapshot
    assert view.type_combo.currentText() == "serial"
    assert view.daemon_check.isChecked() is False
    assert view.dev_path_edit.text() == "/dev/ttyUSB1"
    assert view.dev_host_edit.text() == ""  # host: null -> empty
    assert view.long_name_edit.text() == "W1AW Mesh"
    assert view.short_name_edit.text() == "W1AW"
    qtbot.keyClicks(view.short_name_edit, "X")  # user edit -> dirty
    view.daemon_check.click()  # user toggle -> dirty (clicked, not setChecked)
    with qtbot.waitSignal(view.changed, timeout=5000):
        view.mesh_save_btn.click()
    assert calls == [
        ("serial", "/dev/ttyUSB1", "", "W1AW Mesh", "W1AWX", "US", "LONG_FAST", "", "true")
    ]
    assert "mesh updated:" in view.save_status.text()


def test_profiles_mesh_save_failure_surfaces(qtbot):
    def boom(dt, dp, dh, ln, sn, rg, mp, cu, de):
        raise RuntimeError("mesh.device.path is required when device.type is serial")

    services = _spy_services(_fake_services(), mesh_save=boom)
    view = ProfilesView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    with qtbot.waitSignal(view.changed, timeout=5000):
        view.mesh_save_btn.click()
    assert "FAILED" in view.save_status.text()
    assert "mesh.device.path is required" in view.save_status.text()


# --------------------------------------------------- M5: install col + wine hub + tray
def _wine_buttons(view):
    """(start, stop) QPushButtons of the first wine-hub app row."""
    from PySide6.QtWidgets import QPushButton

    btns = []
    for i in range(view.winehub_btns.count()):
        w = view.winehub_btns.itemAt(i).widget()
        if isinstance(w, QPushButton):
            btns.append(w)
    return btns[0], btns[1]


def test_catalog_has_install_column_and_winehub_panel(qtbot):
    view = CatalogView(_fake_services())
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    assert view.model.columnCount() == 6
    headers = [view.model.headerData(i, Qt.Orientation.Horizontal) for i in range(6)]
    assert "install" in headers
    col = headers.index("install")
    assert view.model.index(0, col).data() == "installed"  # ardop sorts first
    assert "prefix present" in view.winehub_label.text()
    assert "com5->/dev/ttyUSB0" in view.winehub_label.text()
    start, stop = _wine_buttons(view)
    assert start.isEnabled() and not stop.isEnabled()  # vara-hf stopped, gate open


def test_catalog_winehub_panel_disabled_without_prefix_or_wine(qtbot):
    snap = _snap()
    snap["winehub"] = dict(snap["winehub"], prefix=False, wine=None)
    view = CatalogView(_fake_services())
    qtbot.addWidget(view)
    view.set_snapshot(snap)
    assert "ABSENT" in view.winehub_label.text()
    start, stop = _wine_buttons(view)
    assert not start.isEnabled() and not stop.isEnabled()


def test_catalog_winehub_start_action_runs_off_ui_thread(qtbot):
    calls = []
    services = _spy_services(
        _fake_services(),
        winehub_start=lambda app_id: (
            calls.append(app_id),
            {"id": app_id, "state": "running", "pid": 4242},
        )[1],
    )
    view = CatalogView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    start, _stop = _wine_buttons(view)
    with qtbot.waitSignal(view.changed, timeout=5000):
        start.click()
    assert calls == ["vara-hf"]
    assert "running: vara-hf" in view.winehub_label.text()


def test_catalog_winehub_stop_and_failure_surfaces(qtbot):
    def boom(app_id):
        raise RuntimeError("wine prefix not installed yet")

    services = _spy_services(_fake_services(), winehub_stop=boom)
    view = CatalogView(services)
    qtbot.addWidget(view)
    snap = _snap()
    snap["winehub"]["apps"][0]["running"] = True
    view.set_snapshot(snap)
    _start, stop = _wine_buttons(view)
    assert stop.isEnabled()
    with qtbot.waitSignal(view.changed, timeout=5000):
        stop.click()
    assert "FAILED" in view.winehub_label.text()
    assert "wine prefix not installed yet" in view.winehub_label.text()


def test_main_window_tray_is_noop_offscreen(qtbot):
    win = MainWindow(_fake_services(), autostart=False)
    qtbot.addWidget(win)
    assert win.tray.available is False  # offscreen: no system-tray host
    win.set_snapshot(_snap())  # tray.update must stay a safe no-op
    assert win.tray.available is False
