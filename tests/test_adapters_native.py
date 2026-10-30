# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Config adapter tests (P4 M7): the native apps' Linux-side config (§6.2).

Same contract the VARA tests pin (read masks, apply diffs/backs up/atomically
writes, dry-run writes nothing, tokens refuse), now over JSON / INI /
properties / YAML / XML / launch-args — plus the registry wiring: all twelve
apps resolve through ``get_adapter`` with pinned native paths and
``state: active``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from core import registry  # noqa: E402
from core.adapters import ConfigError, get_adapter  # noqa: E402
from core.adapters.ardop_args import ArdopArgsAdapter  # noqa: E402
from core.adapters.ini_conf import (  # noqa: E402
    ArtemisPathsAdapter,
    ParaconConfAdapter,
    QtTermTCPConfAdapter,
    XyGribConfAdapter,
)
from core.adapters.json_conf import (  # noqa: E402
    ChattervoxConfAdapter,
    GridtrackerConfAdapter,
    PatConfAdapter,
    SdrppConfAdapter,
)
from core.adapters.meshtasticd_conf import MeshtasticdYamlAdapter  # noqa: E402
from core.adapters.native import JsonDoc, PropsDoc, mask_tree  # noqa: E402
from core.adapters.navit_conf import NavitXmlAdapter  # noqa: E402
from core.adapters.yaac_props import YaacPrefsAdapter  # noqa: E402

PROFILE = {"profile": {"id": "default", "callsign": "W1AW", "grid": "FN31pr"}}
EMPTY_CALLSIGN = {"profile": {"callsign": "", "grid": "FN31pr"}}


@pytest.fixture
def box(tmp_path, monkeypatch):
    """Fake station: /opt root, HOME, recipes dir; XDG envs pinned off."""
    root, home, recipes = tmp_path / "opt" / "grid-down", tmp_path / "home", tmp_path / "recipes"
    recipes.mkdir(parents=True)
    home.mkdir(parents=True)
    state = tmp_path / "state"
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    def recipe(app_id: str, cfg: dict | None = None) -> None:
        body = {"recipe": {"id": app_id, "strategy": "tarball"}}
        if cfg is not None:
            body["recipe"]["config"] = cfg
        (recipes / f"{app_id}.yaml").write_text(yaml.safe_dump(body))

    def install(app_id: str, marker: str = "marker", under: str | None = None) -> Path:
        p = root / (under or app_id) / marker
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        return p

    def make(cls, app_id: str, cfg: dict | None = None, **kw):
        recipe(app_id, cfg)
        app = {"id": app_id, "config": {"adapter": "?"}}
        return cls(app, install_root=root, home=home, recipes_dir=recipes, state_dir=state, **kw)

    return SimpleNamespace(
        tmp=tmp_path,
        root=root,
        home=home,
        recipes=recipes,
        state=state,
        recipe=recipe,
        install=install,
        make=make,
    )


# -- registry wiring ----------------------------------------------------------


def test_registry_resolves_all_twelve_native_adapters():
    apps = {a["id"]: a for a in registry.load_registry(ROOT / "control" / "registry" / "apps")}
    expected = {
        "ardop": ArdopArgsAdapter,
        "pat": PatConfAdapter,
        "qttermtcp": QtTermTCPConfAdapter,
        "paracon": ParaconConfAdapter,
        "sdrpp": SdrppConfAdapter,
        "gridtracker": GridtrackerConfAdapter,
        "yaac": YaacPrefsAdapter,
        "osm-navit": NavitXmlAdapter,
        "xygrib": XyGribConfAdapter,
        "artemis": ArtemisPathsAdapter,
        "meshtasticd": MeshtasticdYamlAdapter,
        "chattervox": ChattervoxConfAdapter,
    }
    assert set(expected) <= set(apps), "a native app went missing from the registry"
    for app_id, cls in expected.items():
        app = apps[app_id]
        assert app.get("state") == "active", app_id
        assert "TBD" not in str(app["config"].get("native_path")), app_id
        assert isinstance(get_adapter(app), cls), app_id


def test_shipped_recipes_declare_config_blocks():
    rec = ROOT / "install" / "recipes"

    def cfg(app_id):
        data = yaml.safe_load((rec / f"{app_id}.yaml").read_text())
        return data["recipe"].get("config") or {}

    rows = cfg("pat").get("set") or []
    assert [r["key"] for r in rows] == ["mycall", "locator", "ardop.addr"]
    assert len(cfg("paracon").get("set") or []) >= 2
    assert cfg("meshtasticd").get("set")
    assert cfg("ardop").get("args") == []
    for app_id in (
        "qttermtcp",
        "xygrib",
        "artemis",
        "gridtracker",
        "sdrpp",
        "chattervox",
        "yaac",
        "osm-navit",
    ):
        assert (cfg(app_id).get("set") or []) == [], app_id


# -- shared machinery ---------------------------------------------------------


def test_mask_tree_masks_credential_shaped_keys_recursively():
    masked, hit = mask_tree({"a": 1, "password": "x", "list": [{"apikey": "k"}, {"b": 2}]})
    assert masked == {"a": 1, "password": "***", "list": [{"apikey": "***"}, {"b": 2}]}
    assert hit is True
    assert mask_tree({"a": 1}) == ({"a": 1}, False)


def test_props_doc_parses_simple_lines_and_refuses_continuations(tmp_path):
    p = tmp_path / "x.properties"
    p.write_text("# comment\n!also comment\nkey=a\\\n b c\nother=1\n", encoding="utf-8")
    doc = PropsDoc(p)
    assert doc.properties() == {"key": "a", "other": "1"}
    with pytest.raises(ConfigError, match="continuation"):
        doc.set("key", "z")
    doc.set("other", "2")  # simple keys patch fine
    assert "other=2" in doc.dumps()


def test_json_doc_refuses_invalid_json_and_sets_dotted(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{oops", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        JsonDoc(p)
    doc = JsonDoc(tmp_path / "absent.json")
    doc.set("a.b.c", 1)
    assert json.loads(doc.dumps()) == {"a": {"b": {"c": 1}}}


# -- JSON family --------------------------------------------------------------


def test_json_refuses_when_app_is_not_installed(box):
    ada = box.make(PatConfAdapter, "pat")
    with pytest.raises(ConfigError, match="not installed"):
        ada.read()


def test_json_read_masks_and_reports_registration(box):
    box.install("pat")
    cfg_dir = box.home / ".config" / "pat"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"mycall": "OLD", "password": "s3cret", "secure": {"apikey": "k"}})
    )
    info = box.make(PatConfAdapter, "pat").read()
    assert info["exists"] is True and info["format"] == "json"
    assert info["path"].endswith(".config/pat/config.json")
    assert info["values"]["mycall"] == "OLD"
    assert info["values"]["password"] == "***" and info["values"]["secure"]["apikey"] == "***"
    assert info["registration"] == "present (values masked)"


def test_json_read_without_config_yet(box):
    box.install("pat")
    info = box.make(PatConfAdapter, "pat").read()
    assert info["exists"] is False and info["values"] == {}
    assert info["registration"] == "absent"


def test_json_apply_backs_up_writes_and_is_idempotent(box):
    box.install("pat")
    cfg = box.home / ".config" / "pat" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"telnet": {"listeners": [{"port": 8777}]}}))
    ada = box.make(
        PatConfAdapter,
        "pat",
        {
            "set": [
                {"key": "mycall", "value": "{callsign}"},
                {"key": "locator", "value": "{grid}"},
                {"key": "ardop.addr", "value": "127.0.0.1:8100"},
            ]
        },
    )
    out = ada.apply(PROFILE)
    assert out["wrote"] is True and out["changed"] is True
    assert out["keys"] == ["mycall", "locator", "ardop.addr"]
    data = json.loads(cfg.read_text())
    assert data["telnet"]["listeners"] == [{"port": 8777}]  # untouched keys survive
    assert data["mycall"] == "W1AW" and data["locator"] == "FN31pr"
    backups = list((box.state / "config-backups" / "pat").glob("*-config.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text())["telnet"]
    again = ada.apply(PROFILE)
    assert again["changed"] is False and again["note"].startswith("already in sync")
    assert len(list((box.state / "config-backups" / "pat").glob("*"))) == 1


def test_json_apply_creates_missing_file_with_no_backup(box):
    box.install("pat")
    ada = box.make(PatConfAdapter, "pat", {"set": [{"key": "mycall", "value": "{callsign}"}]})
    out = ada.apply(PROFILE)
    assert out["wrote"] is True and "backup" not in out
    cfg = box.home / ".config" / "pat" / "config.json"
    assert json.loads(cfg.read_text()) == {"mycall": "W1AW"}


def test_json_apply_dry_run_writes_nothing(box):
    box.install("pat")
    cfg = box.home / ".config" / "pat" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{}\n")
    ada = box.make(PatConfAdapter, "pat", {"set": [{"key": "mycall", "value": "{callsign}"}]})
    out = ada.apply(PROFILE, dry_run=True)
    assert out["changed"] is True and out["dry_run"] is True and "wrote" not in out
    assert json.loads(cfg.read_text()) == {}


def test_json_refuses_invalid_json_and_bad_rows_and_tokens(box):
    box.install("pat")
    cfg = box.home / ".config" / "pat" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{oops")
    ada = box.make(PatConfAdapter, "pat", {"set": [{"key": "mycall", "value": "x"}]})
    with pytest.raises(ConfigError, match="not valid JSON"):
        ada.read()
    cfg.write_text("{}\n")
    with pytest.raises(ConfigError, match="non-empty key"):
        box.make(PatConfAdapter, "pat", {"set": [{"key": "  ", "value": "x"}]}).apply(PROFILE)
    bad = box.make(PatConfAdapter, "pat", {"set": ["not-a-row"]})
    with pytest.raises(ConfigError, match="mapping"):
        bad.apply(PROFILE)
    tok = box.make(PatConfAdapter, "pat", {"set": [{"key": "k", "value": "{rig}"}]})
    with pytest.raises(ConfigError, match="unknown template"):
        tok.apply(PROFILE)
    empty = box.make(PatConfAdapter, "pat", {"set": [{"key": "mycall", "value": "{callsign}"}]})
    with pytest.raises(ConfigError, match="callsign"):
        empty.apply(EMPTY_CALLSIGN)


def test_json_pat_and_xygrib_honor_xdg_config_home(box, monkeypatch):
    xdg = box.tmp / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert box.make(PatConfAdapter, "pat").native_path() == xdg / "pat" / "config.json"
    assert box.make(XyGribConfAdapter, "xygrib").native_path() == (
        xdg / "openGribs" / "XyGrib.conf"
    )


def test_json_paths_pinned_per_app(box):
    box.install("chattervox")
    assert box.make(ChattervoxConfAdapter, "chattervox").native_path() == (
        box.home / ".chattervox" / "config.json"
    )
    (box.root / "sdrpp" / "dist").mkdir(parents=True)
    assert box.make(SdrppConfAdapter, "sdrpp").native_path() == (
        box.root / "sdrpp" / "dist" / "config.json"
    )
    assert box.make(GridtrackerConfAdapter, "gridtracker").native_path() == (
        box.home / ".config" / "GridTracker2" / "app-settings.json"
    )


# -- INI family ---------------------------------------------------------------


def test_ini_qttermtcp_read_masks_applies_and_keeps_order(box):
    box.install("qttermtcp", marker="QtTermTCP")
    ini = box.root / "qttermtcp" / "QtTermTCP.ini"
    ini.write_text("[Setup]\nMyCall=OLD\nRegKey=SECRET-1\n", encoding="utf-8")
    ada = box.make(
        QtTermTCPConfAdapter,
        "qttermtcp",
        {
            "set": [
                {"section": "Setup", "key": "PTTPort", "value": "COM5"},
                {"section": "Setup", "key": "MyCall", "value": "{callsign}"},
            ]
        },
    )
    info = ada.read()
    assert info["path"].endswith("qttermtcp/QtTermTCP.ini")
    assert info["sections"]["Setup"]["RegKey"] == "***"
    assert info["registration"] == "present (values masked)"
    out = ada.apply(PROFILE)
    assert out["wrote"] is True and out["keys"] == ["Setup:PTTPort", "Setup:MyCall"]
    text = ini.read_text()
    assert text.index("MyCall=W1AW") < text.index("RegKey=SECRET-1") < text.index("PTTPort=COM5")
    backups = list((box.state / "config-backups" / "qttermtcp").glob("*-QtTermTCP.ini"))
    assert len(backups) == 1 and "SECRET-1" in backups[0].read_text()
    again = ada.apply(PROFILE)
    assert again["changed"] is False and again["note"].startswith("already in sync")
    assert len(list((box.state / "config-backups" / "qttermtcp").glob("*"))) == 1


def test_ini_qttermtcp_refuses_empty_section_or_key(box):
    box.install("qttermtcp", marker="QtTermTCP")
    ada = box.make(
        QtTermTCPConfAdapter, "qttermtcp", {"set": [{"section": "", "key": "K", "value": "v"}]}
    )
    with pytest.raises(ConfigError, match="non-empty"):
        ada.apply(PROFILE)


def test_ini_xygrib_and_paracon_paths(box):
    box.install("xygrib", marker="xyGrib")
    ada = box.make(XyGribConfAdapter, "xygrib")
    assert ada.native_path() == box.home / ".config" / "openGribs" / "XyGrib.conf"
    box.install("paracon", marker="paracon")
    p_ada = box.make(ParaconConfAdapter, "paracon")
    assert p_ada.native_path() == box.root / "paracon" / "paracon.cfg"
    alt = box.make(ParaconConfAdapter, "paracon", run_dir=box.tmp / "alt")
    assert alt.native_path() == box.tmp / "alt" / "paracon.cfg"


def test_ini_paracon_apply_uses_upstream_verified_rows(box):
    box.install("paracon", marker="paracon")
    ada = box.make(
        ParaconConfAdapter,
        "paracon",
        {
            "set": [
                {"section": "Setup", "key": "callsign", "value": "{callsign}"},
                {"section": "Unproto", "key": "source", "value": "{callsign}"},
            ]
        },
    )
    out = ada.apply(PROFILE)
    assert out["wrote"] is True
    text = (box.root / "paracon" / "paracon.cfg").read_text()
    assert "[Setup]" in text and "callsign=W1AW" in text
    assert "[Unproto]" in text and "source=W1AW" in text


def test_ini_artemis_honors_xdg_data_home_and_config_file(box, monkeypatch):
    box.install("artemis")
    monkeypatch.setenv("XDG_DATA_HOME", str(box.tmp / "xdgdata"))
    ada = box.make(ArtemisPathsAdapter, "artemis", {"set": [], "file": "custom.conf"})
    assert ada.native_path() == (box.tmp / "xdgdata" / "AresValley" / "Artemis" / "custom.conf")
    info = ada.read()
    assert info["prefs_dir"].endswith("AresValley/Artemis")
    assert info["exists"] is False and info["registration"] == "absent"


# -- Java properties (yaac) ---------------------------------------------------


PROPS_TEXT = (
    "# YAAC configuration (edited by gd)\n"
    "portop=SerialPort:/dev/ttyUSB0:9600\n"
    "DebugFile=/tmp/yaac.log\n"
    "KeepMe=1\n"
)


def test_yaac_read_parses_and_masks(box):
    box.install("yaac", marker="YAAC.jar")
    props = box.home / ".yaac" / "yaac.properties"
    props.parent.mkdir(parents=True)
    props.write_text(PROPS_TEXT + "password=zzz\n", encoding="utf-8")
    info = box.make(YaacPrefsAdapter, "yaac").read()
    assert info["path"].endswith(".yaac/yaac.properties")
    assert info["values"]["portop"] == "SerialPort:/dev/ttyUSB0:9600"
    assert info["values"]["password"] == "***"
    assert info["registration"] == "present (values masked)"


def test_yaac_apply_patches_in_place_appends_and_is_idempotent(box):
    box.install("yaac", marker="YAAC.jar")
    props = box.home / ".yaac" / "yaac.properties"
    props.parent.mkdir(parents=True)
    props.write_text(PROPS_TEXT, encoding="utf-8")
    ada = box.make(
        YaacPrefsAdapter,
        "yaac",
        {
            "set": [
                {"key": "DebugFile", "value": "/var/log/yaac.log"},
                {"key": "mycall", "value": "{callsign}"},
            ]
        },
    )
    out = ada.apply(PROFILE)
    assert out["wrote"] is True
    text = props.read_text()
    lines = text.splitlines()
    assert lines[0] == "# YAAC configuration (edited by gd)"  # comment survived
    assert "portop=SerialPort:/dev/ttyUSB0:9600" in lines  # untouched line intact
    assert lines.index("DebugFile=/var/log/yaac.log") < lines.index("KeepMe=1")
    assert lines[-1] == "mycall=W1AW"  # appended at the end
    assert text.endswith("\n")
    backups = list((box.state / "config-backups" / "yaac").glob("*-yaac.properties"))
    assert len(backups) == 1 and "/tmp/yaac.log" in backups[0].read_text()
    again = ada.apply(PROFILE)
    assert again["changed"] is False and again["note"].startswith("already in sync")
    assert props.read_text() == text  # byte-identical


def test_yaac_refuses_continuation_keys(box):
    box.install("yaac", marker="YAAC.jar")
    props = box.home / ".yaac" / "yaac.properties"
    props.parent.mkdir(parents=True)
    props.write_text("multi=a\\\n b c\n", encoding="utf-8")
    ada = box.make(YaacPrefsAdapter, "yaac", {"set": [{"key": "multi", "value": "x"}]})
    with pytest.raises(ConfigError, match="continuation"):
        ada.apply(PROFILE)


# -- YAML drop-in (meshtasticd) ----------------------------------------------


def test_meshtasticd_writes_only_its_drop_in(box):
    etc = box.tmp / "etc" / "meshtasticd"
    etc.mkdir(parents=True)
    upstream = etc / "config.yaml"
    upstream.write_text("Lora:\n  Module: sx1268\n", encoding="utf-8")
    ada = box.make(
        MeshtasticdYamlAdapter,
        "meshtasticd",
        {"set": [{"key": "Lora.Module", "value": "auto"}]},
        root=box.tmp,
    )
    info = ada.read()
    assert info["exists"] is False and info["note"].startswith("drop-in")
    out = ada.apply(PROFILE)
    assert out["wrote"] is True and "backup" not in out
    drop = etc / "config.d" / "99-grid-down.yaml"
    assert yaml.safe_load(drop.read_text()) == {"Lora": {"Module": "auto"}}
    assert upstream.read_text() == "Lora:\n  Module: sx1268\n"  # upstream untouched
    again = ada.apply(PROFILE)
    assert again["changed"] is False and again["note"].startswith("already in sync")


def test_meshtasticd_refuses_without_etc_dir_and_bad_rows(box):
    ada = box.make(
        MeshtasticdYamlAdapter,
        "meshtasticd",
        {"set": [{"key": "Lora.Module", "value": "auto"}]},
        root=box.tmp,
    )
    with pytest.raises(ConfigError, match="not installed"):
        ada.read()
    (box.tmp / "etc" / "meshtasticd").mkdir(parents=True)
    bad = box.make(
        MeshtasticdYamlAdapter, "meshtasticd", {"set": [{"key": "", "value": "x"}]}, root=box.tmp
    )
    with pytest.raises(ConfigError, match="non-empty key"):
        bad.apply(PROFILE)


# -- XML overlay (navit) ------------------------------------------------------

SEED = (
    '<?xml version="1.0"?>\n'
    '<navit>\n  <vehicle name="Local GPS" source="gpsd://localhost:2947"/>\n</navit>\n'
)
GPS_ROW = {
    "xpath": ".//vehicle[@name='Local GPS']",
    "attribute": "source",
    "value": "gpsd://127.0.0.1:2947",
}


def navit(box, rows):
    return box.make(NavitXmlAdapter, "osm-navit", {"set": rows}, root=box.tmp)


def test_navit_seeds_from_system_default_then_patches(box):
    box.install("osm-navit", marker="navit", under="navit")
    seed = box.tmp / "usr" / "share" / "navit" / "navit.xml"
    seed.parent.mkdir(parents=True)
    seed.write_text(SEED, encoding="utf-8")
    ada = navit(box, [GPS_ROW])
    info = ada.read()
    assert info["exists"] is False and "seeded" in info["note"]
    out = ada.apply(PROFILE)
    assert out["wrote"] is True and "backup" not in out  # nothing existed to back up
    user = box.home / ".navit" / "navit.xml"
    text = user.read_text()
    assert text.startswith('<?xml version="1.0"?>')
    assert 'source="gpsd://127.0.0.1:2947"' in text
    assert seed.read_text() == SEED  # system default untouched
    again = ada.apply(PROFILE)
    assert again["changed"] is False and again["note"].startswith("already in sync")
    assert ada.read()["values"] == [
        {"xpath": GPS_ROW["xpath"], "attribute": "source", "value": "gpsd://127.0.0.1:2947"}
    ]


def test_navit_dry_run_and_refusals(box):
    box.install("osm-navit", marker="navit", under="navit")
    seed = box.tmp / "usr" / "share" / "navit" / "navit.xml"
    seed.parent.mkdir(parents=True)
    seed.write_text(SEED, encoding="utf-8")
    ada = navit(box, [GPS_ROW])
    out = ada.apply(PROFILE, dry_run=True)
    assert out["changed"] is True and "wrote" not in out
    assert not (box.home / ".navit" / "navit.xml").exists()
    missing = navit(
        box, [{"xpath": ".//vehicle[@name='Nope']", "attribute": "source", "value": "x"}]
    )
    with pytest.raises(ConfigError, match="matches no element"):
        missing.apply(PROFILE)
    abs_path = navit(box, [{"xpath": "/navit/vehicle", "attribute": "source", "value": "x"}])
    with pytest.raises(ConfigError, match="relative"):
        abs_path.apply(PROFILE)
    seed.unlink()
    with pytest.raises(ConfigError, match="seed from"):
        ada.apply(PROFILE)


# -- launch args (ardop) ------------------------------------------------------


def test_ardop_writes_reads_and_is_idempotent(box):
    box.install("ardop", marker="ardopcf")
    ada = box.make(ArdopArgsAdapter, "ardop", {"args": ["8515", "{callsign}-cap", "--wb"]})
    out = ada.apply(PROFILE)
    assert out["wrote"] is True and out["args"] == ["8515", "W1AW-cap", "--wb"]
    assert "backup" not in out  # the args file didn't exist yet
    args_file = box.state / "app-args" / "ardop.args"
    assert args_file.read_text() == "8515\nW1AW-cap\n--wb\n"
    info = ada.read()
    assert info["format"] == "launch-args" and info["args"] == ["8515", "W1AW-cap", "--wb"]
    assert "no config file" in info["registration"]
    # a later change backs the previous file up before rewriting it
    box.recipe("ardop", {"args": ["8515", "{callsign}-cap"]})
    out2 = ada.apply(PROFILE)
    assert out2["wrote"] is True and out2["args"] == ["8515", "W1AW-cap"]
    backups = list((box.state / "config-backups" / "ardop").glob("*-ardop.args"))
    assert len(backups) == 1 and "--wb" in backups[0].read_text()
    assert ada.read()["args"] == ["8515", "W1AW-cap"]
    assert ada.apply(PROFILE)["changed"] is False
    assert len(list((box.state / "config-backups" / "ardop").glob("*"))) == 1


def test_ardop_note_when_undeclared_refusals_and_dry_run(box):
    box.install("ardop", marker="ardopcf")
    ada = box.make(ArdopArgsAdapter, "ardop")
    out = ada.apply(PROFILE)
    assert out["changed"] is False and "declares no config.args" in out["note"]
    tok = box.make(ArdopArgsAdapter, "ardop", {"args": ["{rig}"]})
    with pytest.raises(ConfigError, match="unknown template"):
        tok.apply(PROFILE)
    dry = box.make(ArdopArgsAdapter, "ardop", {"args": ["8515"]})
    dout = dry.apply(PROFILE, dry_run=True)
    assert dout["changed"] is True and "wrote" not in dout
    assert not (box.state / "app-args" / "ardop.args").exists()


def test_ardop_refuses_when_not_installed(box):
    ada = box.make(ArdopArgsAdapter, "ardop", {"args": ["8515"]})
    with pytest.raises(ConfigError, match="not installed"):
        ada.apply(PROFILE)
