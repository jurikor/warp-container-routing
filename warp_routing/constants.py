"""Shared constants for WARP container routing."""

from __future__ import annotations

import pathlib


APP_NAME = "warp-container-routing"
CONFIG_DIR = pathlib.Path("/etc/warp-container-routing")
HELPER_PATH = pathlib.Path("/usr/local/sbin/warp-container-routing-helper")
SYSTEMD_UNIT = pathlib.Path("/etc/systemd/system/warp-container-routing@.service")
DEFAULT_PROFILE = "wgcf"
DEFAULT_BASE_TABLE = 52000
DEFAULT_BASE_PRIORITY = 11000
DEFAULT_EXTERNAL_URL = "https://cloudflare.com/cdn-cgi/trace"
DEFAULT_WARP_ENDPOINT = "162.159.192.1:2408"
