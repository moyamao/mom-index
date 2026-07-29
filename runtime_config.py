from __future__ import annotations

import configparser
import os
from typing import Optional


PROJECT_ROOT = os.path.dirname(__file__)
DEFAULT_CONFIG_FILE = os.path.join(PROJECT_ROOT, "conf", "config.ini")


def _parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    path = os.environ.get("MOM_INDEX_CONFIG_FILE", DEFAULT_CONFIG_FILE).strip() or DEFAULT_CONFIG_FILE
    if os.path.exists(path):
        parser.read(path, encoding="utf-8")
    return parser


def ini_get(section: str, option: str, fallback: str = "") -> str:
    parser = _parser()
    if not parser.has_section(section):
        return fallback
    return parser.get(section, option, fallback=fallback).strip()


def ini_get_bool(section: str, option: str, fallback: bool) -> bool:
    raw = ini_get(section, option, "")
    if not raw:
        return fallback
    return raw.lower() in {"1", "true", "yes", "on"}


def ini_get_int(section: str, option: str, fallback: int) -> int:
    raw = ini_get(section, option, "")
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def ini_get_float(section: str, option: str, fallback: float) -> float:
    raw = ini_get(section, option, "")
    if not raw:
        return fallback
    try:
        return float(raw)
    except ValueError:
        return fallback


def ini_get_optional(section: str, option: str) -> Optional[str]:
    value = ini_get(section, option, "")
    return value or None
