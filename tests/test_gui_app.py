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
        "profile": {"path": "/etc/grid-down/profiles/default.yaml", "id": "default",
                    "callsign": "W1AW", "grid": "FN31pr"},
        "rig": {"argv": ["rigctld", "-T", "127.0.0.1", "-t", "4532", "-v", "-m", "1"],
                "notes": [], "endpoint": "127.0.0.1:4532",
                "model": "3073", "path": "/dev/ttyUSB0", "baud": "38400"},
        "audio": {"present": ["grid_down_hf_out"], "missing": ["grid_down_hf_in"]},
        "instances": {"apps": [{"id": "hamlib", "state": "running", "pid": 42}],
                      "bundles": {"winlink": {"running": ["hamlib", "ardop"],
                                              "total": 3},
                                  "js8": {"running": [], "total": 2}}},
        "bundles": {"js8": {"description": "JS8 weak-signal messaging",
                            "members": ["hamlib", "js8call"]},
                    "packet": {"description": "Packet radio terminal stack",
                               "members": ["direwolf", "qttermtcp", "paracon"]},
                    "winlink": {"description": "HF/VHF email via Winlink",
                                "members": ["hamlib", "direwolf", "ardop", "pat"]}},
        "profile_raw": {"profile": {"id": "default", "callsign": "W1AW",
                                    "grid": "FN31pr"},
                        "rig": {"model": "", "path": "", "baud": ""}},
        "apps": {"ardop": {"name": "ARDOP TNC", "category": "winlink",
                           "launch": {"via": "managed-service"},
                           "ui": {"tagline": "ARDOP modem"}},
                 "direwolf": {"name": "Direwolf", "category": "packet",
                              "launch": {"via": "managed-service"}, "ui": {}},
                 "js8call": {"name": "JS8Call", "category": "winlink", "ui": {}}},
        "doctor": {"results": [], "counts": {"ok": 8, "warn": 5, "fail": 0, "skip": 4},
                   "all_ok": True},
        "errors": {},
    }


def _fake_services() -> Services:
    snap = _snap()
    return Services(version=lambda: snap["version"],
                    profile=lambda: snap["profile"],
                    profile_raw=lambda: snap["profile_raw"],
                    rig=lambda: snap["rig"],
                    audio=lambda: snap["audio"],
                    instances=lambda: snap["instances"],
                    apps=lambda: snap["apps"],
                    bundles=lambda: snap["bundles"],
                    doctor=lambda: snap["doctor"]["results"],
                    bundle_start=lambda name: {"bundle": name,
                                               "notes": [f"running: {name}"]},
                    bundle_stop=lambda name: {"bundle": name,
                                              "notes": [f"stopped: {name}"]},
                    audio_up=lambda: {"created": [{"endpoint": "grid_down_hf_out",
                                                   "module": 7}],
                                      "skipped": ["grid_down_hf_in"]},
                    audio_down=lambda: {"unloaded": ["grid_down_hf_out"]})


def _spy_services(base: Services, **over) -> Services:
    """Copy a Services with selected callables replaced (test spy)."""
    fields = {f: getattr(base, f) for f in
              ("version", "profile", "profile_raw", "rig", "audio", "instances",
               "apps", "bundles", "doctor", "bundle_start", "bundle_stop",
               "audio_up", "audio_down")}
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
        bundle_start=lambda n: (calls.append(n),
                                {"bundle": n, "notes": [f"running: {n}"]})[1])
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
    services = _spy_services(_fake_services(),
                             audio_up=lambda: (calls.append("up"),
                                               {"created": [{"endpoint": "e",
                                                             "module": 7}],
                                                "skipped": []})[1])
    view = DashboardView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    with qtbot.waitSignal(view.refreshed, timeout=5000):
        view._audio_up()
    assert calls == ["up"]


def test_dashboard_audio_down_requires_confirm(qtbot):
    calls = []
    services = _spy_services(_fake_services(),
                             audio_down=lambda: (calls.append("down"),
                                                 {"unloaded": []})[1])
    view = DashboardView(services)
    qtbot.addWidget(view)
    view.set_snapshot(_snap())
    view._confirm = lambda t, x: False
    view._audio_down()
    assert calls == []          # refused — no worker, no core call
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
    view.filter_box.setText("ardop")      # id match
    assert view.proxy.rowCount() == 1
    view.filter_box.setText("packet")     # category match
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
    assert view.model.data(view.model.index(0, 4)) == "-"      # not running
    assert view.model.data(view.model.index(0, 0), 3) == "ARDOP modem"  # tooltip
    assert view.model.data(view.model.index(2, 0), 3) is None  # js8call: none


def test_doctor_rows_colors_and_summary(qtbot):
    view = DoctorView(_fake_services())
    qtbot.addWidget(view)
    snap = _snap()
    snap["doctor"]["results"] = [
        {"id": "platform", "title": "platform", "status": "ok", "detail": "linux"},
        {"id": "ports", "title": "port conflicts", "status": "fail",
         "detail": "4532 busy"},
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
    snap["doctor"]["results"] = [{"id": "ports", "title": "port conflicts",
                                  "status": "fail", "detail": "4532 busy"}]
    view.set_snapshot(snap)
    checks = json.loads(view.copy_json())["checks"]
    assert checks[0]["id"] == "ports" and checks[0]["status"] == "fail"
