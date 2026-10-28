# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Config adapter tests (P3): the VARA family's exe-adjacent INIs (§6.2)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from core import adapters  # noqa: E402
from core.adapters import ConfigError, get_adapter  # noqa: E402
from core.winehub import WineHub  # noqa: E402

REGISTRY = {
    "vara-hf": {
        "id": "vara-hf",
        "config": {"adapter": "vara-ini"},
        "launch": {"via": "wine-hub", "cmd": "VARA.exe (via gd winehub)"},
    },
    "varac": {
        "id": "varac",
        "config": {"adapter": "varac-ini"},
        "launch": {"via": "wine-hub", "cmd": "VarAC.exe"},
    },
    "hamlib": {"id": "hamlib", "config": {"adapter": "none"}},
}

PROFILE = {"profile": {"id": "default", "callsign": "W1AW", "grid": "FN31pr"}}


@pytest.fixture
def station(tmp_path):
    """Fake prefix + recipes dir + adapter factory, mirroring test_winehub."""
    prefix, recipes, state = tmp_path / "prefix", tmp_path / "recipes", tmp_path / "state"
    recipes.mkdir()

    def install_exe(app_id: str, exe_rel: str, ini_text: str | None = None) -> Path:
        exe = prefix / "drive_c" / exe_rel
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"MZ")
        if ini_text is not None:
            exe.with_name(Path(exe_rel).stem + ".ini").write_text(ini_text, encoding="cp1252")
        return exe

    def make(app_id: str, ini_set=None):
        wine = {
            "prefix": str(prefix),
            "installer_glob": "VARA*",
            "exes": ["VARA.exe"] if app_id == "vara-hf" else ["VarAC.exe"],
        }
        if ini_set is not None:
            wine["ini_set"] = ini_set
        (recipes / f"{app_id}.yaml").write_text(
            yaml.safe_dump({"recipe": {"id": app_id, "strategy": "wine", "wine": wine}})
        )
        app = dict(REGISTRY[app_id])
        hub = WineHub([app], state_dir=state, prefix=prefix, recipes_dir=recipes)
        return get_adapter(app, hub=hub, recipes_dir=recipes, state_dir=state)

    return SimpleNamespace(make=make, install_exe=install_exe, prefix=prefix, state=state)


def test_get_adapter_maps_registry_names_and_refuses_the_rest():
    assert isinstance(get_adapter(dict(REGISTRY["vara-hf"])), adapters.VaraIniAdapter)
    assert isinstance(get_adapter(dict(REGISTRY["varac"])), adapters.VaracIniAdapter)
    with pytest.raises(ConfigError, match="no implementation"):
        get_adapter(dict(REGISTRY["hamlib"]))
    with pytest.raises(ConfigError, match="no implementation"):
        get_adapter({"id": "mystery", "config": {"adapter": "made-up"}})


def test_read_masks_registration_and_keeps_other_values(station):
    station.install_exe(
        "vara-hf",
        "VARA HF/VARA.exe",
        "[Common]\nSoundCard=0\nRegStation=W1AW-12345\nRegKey=ABCD-1234\nNote=café\n",
    )
    info = station.make("vara-hf").read()
    assert info["exists"] is True and info["encoding"] == "cp1252"
    assert info["path"].endswith("VARA.ini")
    assert info["exe_path"].endswith("VARA.exe")
    common = info["sections"]["Common"]
    assert common["SoundCard"] == "0" and common["Note"] == "café"
    assert common["RegStation"] == "***" and common["RegKey"] == "***"
    assert info["registration"] == "present (values masked)"


def test_read_reports_absent_registration_and_missing_ini(station):
    station.install_exe("vara-hf", "VARA HF/VARA.exe")
    info = station.make("vara-hf").read()
    assert info["exists"] is False and info["sections"] == {}
    assert info["registration"] == "absent"


def test_read_refuses_when_app_not_installed(station):
    with pytest.raises(ConfigError, match="not installed"):
        station.make("vara-hf").read()


def test_varac_ini_is_derived_from_the_probed_exe(station):
    station.install_exe("varac", "VarAC/VarAC.exe")
    info = station.make("varac").read()
    assert info["path"].endswith("VarAC.ini")


def test_apply_without_declared_keys_is_a_noop_naming_the_recipe(station):
    station.install_exe("vara-hf", "VARA HF/VARA.exe", "[Common]\nSoundCard=0\n")
    out = station.make("vara-hf").apply(PROFILE)
    assert out["changed"] is False and out["diff"] == ""
    assert "ini_set" in out["note"]
    assert "SoundCard=0" in station.prefix.joinpath("drive_c/VARA HF/VARA.ini").read_text()


def test_apply_dry_run_prints_diff_and_writes_nothing(station):
    station.install_exe("vara-hf", "VARA HF/VARA.exe", "[Common]\nSoundCard=0\n")
    ada = station.make(
        "vara-hf",
        ini_set=[{"section": "Common", "key": "PTTPort", "value": "COM5"}],
    )
    out = ada.apply(PROFILE, dry_run=True)
    assert out["changed"] is True and out["dry_run"] is True
    assert "+PTTPort=COM5" in out["diff"] and "[Common]" in out["diff"]
    assert "wrote" not in out
    assert "PTTPort" not in station.prefix.joinpath("drive_c/VARA HF/VARA.ini").read_text()


def test_apply_writes_with_backup_case_and_cp1252_round_trip(station):
    station.install_exe(
        "vara-hf",
        "VARA HF/VARA.exe",
        "[Common]\nSoundCard=0\nNote=café\nRegStation=SECRET\n",
    )
    ini = station.prefix / "drive_c" / "VARA HF" / "VARA.ini"
    ada = station.make(
        "vara-hf",
        ini_set=[
            {"section": "Common", "key": "PTTPort", "value": "COM5"},
            {"section": "Station", "key": "Call", "value": "{callsign}"},
            {"section": "Station", "key": "Locator", "value": "{grid}"},
        ],
    )
    out = ada.apply(PROFILE)
    assert out["wrote"] is True and out["changed"] is True
    assert out["keys"] == ["Common:PTTPort", "Station:Call", "Station:Locator"]

    raw = ini.read_bytes()
    assert b"\xe9" in raw  # cp1252 é survived the rewrite (no utf-8 transcode)
    text = raw.decode("cp1252")
    assert "PTTPort=COM5" in text
    assert "Call=W1AW" in text and "Locator=FN31pr" in text
    assert "SoundCard=0" in text and "RegStation=SECRET" in text  # case + order kept
    assert text.index("SoundCard=0") < text.index("PTTPort=COM5")

    # §6.2: a timestamped backup landed under the state dir before the write
    backups = list((station.state / "config-backups" / "vara-hf").glob("*-VARA.ini"))
    assert len(backups) == 1 and "RegStation=SECRET" in backups[0].read_text(encoding="cp1252")

    # re-apply is in sync, and no second backup appears
    again = ada.apply(PROFILE)
    assert again["changed"] is False and again["note"].startswith("already in sync")
    assert len(list((station.state / "config-backups" / "vara-hf").glob("*"))) == 1


def test_apply_creates_a_missing_ini_before_first_run(station):
    station.install_exe("vara-hf", "VARA HF/VARA.exe")  # no VARA.ini yet
    ada = station.make(
        "vara-hf", ini_set=[{"section": "Common", "key": "PTTPort", "value": "COM5"}]
    )
    out = ada.apply(PROFILE)
    assert out["wrote"] is True and "backup" not in out  # nothing existed to back up
    assert "PTTPort=COM5" in station.prefix.joinpath("drive_c/VARA HF/VARA.ini").read_text()


def test_apply_templates_refuse_empty_profile_fields_and_unknown_tokens(station):
    station.install_exe("vara-hf", "VARA HF/VARA.exe")
    ada = station.make("vara-hf", ini_set=[{"section": "S", "key": "K", "value": "{callsign}"}])
    with pytest.raises(ConfigError, match="callsign"):
        ada.apply({"profile": {"callsign": "", "grid": "FN31pr"}})
    bad = station.make("vara-hf", ini_set=[{"section": "S", "key": "K", "value": "{rig}"}])
    with pytest.raises(ConfigError, match="unknown template"):
        bad.apply(PROFILE)
    rowless = station.make("vara-hf", ini_set=[{"section": "", "key": "K", "value": "x"}])
    with pytest.raises(ConfigError, match="non-empty"):
        rowless.apply(PROFILE)
