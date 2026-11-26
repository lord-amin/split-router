from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "SplitRoute"
APP_ID = "io.github.lord_amin.SplitRoute"
VERSION = "1.0.0"

HELPER_LABEL = "splitroute-helper"
HELPER_BINARY = "/usr/local/libexec/splitroute-helper"
PROBE_BINARY = "/usr/local/libexec/splitroute-probe"
SYSTEMD_UNIT = "/etc/systemd/system/splitroute-helper.service"

SYSTEM_DIR = "/etc/splitroute"
PERSISTENT_RULES = f"{SYSTEM_DIR}/rules.json"
DNS_CACHE = f"{SYSTEM_DIR}/dns-cache.json"
KILLSWITCH_CONFIG = f"{SYSTEM_DIR}/killswitch.json"
LEARNED_SERVERS = f"{SYSTEM_DIR}/learned-vpn-servers.json"
AUTOMATION_CONFIG = f"{SYSTEM_DIR}/automation.json"

RUN_DIR = "/run/splitroute"
SESSION_RULES = f"{RUN_DIR}/session-rules.json"
APPLIED_STATE = f"{RUN_DIR}/applied.json"
STATUS_FILE = f"{RUN_DIR}/status.json"
LOCK_FILE = f"{RUN_DIR}/lock"
NFT_FILE = f"{RUN_DIR}/killswitch.nft"
RUNTIME_STATE = f"{RUN_DIR}/runtime.json"
EVENTS_FILE = f"{RUN_DIR}/events.json"

NFT_TABLE = "inet splitroute"
LOG_FILE = "/var/log/splitroute.log"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_HELPER_BUILD = PROJECT_ROOT / "build" / "splitroute-helper"
LOCAL_PROBE_BUILD = PROJECT_ROOT / "build" / "splitroute-probe"
ICON_PATH = PROJECT_ROOT / "assets" / "splitroute.svg"

XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

CONFIG_DIR = XDG_CONFIG_HOME / "splitroute"
DATA_DIR = XDG_DATA_HOME / "splitroute"
BACKUPS_DIR = DATA_DIR / "Backups"
STAGING_DIR = DATA_DIR / "staging"
WORKSPACE_FILE = CONFIG_DIR / "workspace.json"
LEGACY_WORKSPACE_FILE = XDG_CONFIG_HOME / "blueiprouter" / "workspace.json"
UI_PREFS_FILE = CONFIG_DIR / "ui_prefs.json"
TRAFFIC_HISTORY_FILE = DATA_DIR / "traffic-history.json"
SPEED_HISTORY_FILE = DATA_DIR / "speedtests.json"
AUTOSTART_FILE = XDG_CONFIG_HOME / "autostart" / "splitroute.desktop"


def ensure_user_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, BACKUPS_DIR, STAGING_DIR):
        d.mkdir(parents=True, exist_ok=True)


def find_helper_source_binary() -> Path | None:
    if LOCAL_HELPER_BUILD.is_file() and os.access(LOCAL_HELPER_BUILD, os.X_OK):
        return LOCAL_HELPER_BUILD
    p = Path(HELPER_BINARY)
    if p.is_file() and os.access(p, os.X_OK):
        return p
    return None


def find_probe_binary() -> Path | None:
    for p in (Path(PROBE_BINARY), LOCAL_PROBE_BUILD):
        if p.is_file() and os.access(p, os.X_OK):
            return p
    return None


def find_probe_source_binary() -> Path | None:
    if LOCAL_PROBE_BUILD.is_file() and os.access(LOCAL_PROBE_BUILD, os.X_OK):
        return LOCAL_PROBE_BUILD
    p = Path(PROBE_BINARY)
    if p.is_file() and os.access(p, os.X_OK):
        return p
    return None
