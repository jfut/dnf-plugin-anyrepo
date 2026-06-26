# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

"""Configuration loading and mutation for dnf-plugin-anyrepo."""

import configparser
import glob
import os
import platform
import re
import subprocess
import sys
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse


DEFAULT_CONFIG_PATH = "/etc/dnf/plugins/anyrepo.conf"
INCLUDE_KEY = "include"
DEFAULT_CACHE_DIR = "/var/cache/dnf/anyrepo"
DEFAULT_REFRESH_INTERVAL = 600
DEFAULT_MINIMUM_RELEASE_AGE = 3 * 86400
DEFAULT_DEBUG = False
DEFAULT_SOURCE = "github-release"
DEFAULT_ENABLED = True
DEFAULT_ASSET_INCLUDE = r".*\.rpm$"
DEFAULT_ASSET_EXCLUDE = r"(?:-debuginfo(?:-|[.])|-debugsource(?:-|[.])|[.]src[.]rpm$)"
LEGACY_REPO_KEYS = {
    "asset_regex": "asset_include",
}
MAIN_CONFIG_KEYS = {
    "cache_dir",
    "refresh_interval",
    "minimum_release_age",
    "debug",
    INCLUDE_KEY,
    "asset_include",
    "asset_exclude",
}
REPO_CONFIG_KEYS = {
    "source",
    "url",
    "asset_include",
    "asset_exclude",
    "enabled",
    "minimum_release_age",
    "cache_dir",
    "refresh_interval",
    "arch",
    "releasever",
    "github_token_file",
    "gpgcheck",
}

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$")


class ConfigError(ValueError):
    """Raised when the plugin configuration cannot be used."""


class MainConfig:
    def __init__(
        self,
        cache_dir=DEFAULT_CACHE_DIR,
        refresh_interval=DEFAULT_REFRESH_INTERVAL,
        minimum_release_age=DEFAULT_MINIMUM_RELEASE_AGE,
        debug=DEFAULT_DEBUG,
        include=None,
        asset_include=DEFAULT_ASSET_INCLUDE,
        asset_exclude=DEFAULT_ASSET_EXCLUDE,
    ):
        self.cache_dir = cache_dir
        self.refresh_interval = refresh_interval
        self.minimum_release_age = minimum_release_age
        self.debug = debug
        self.include = include
        self.asset_include = asset_include
        self.asset_exclude = asset_exclude


class RepoConfig:
    def __init__(
        self,
        name,
        source,
        url,
        asset_include,
        enabled,
        minimum_release_age,
        cache_dir,
        refresh_interval,
        asset_exclude=DEFAULT_ASSET_EXCLUDE,
        arch=None,
        releasever=None,
        github_token_file=None,
        gpgcheck=None,
    ):
        self.name = name
        self.source = source
        self.url = url
        self.asset_include = asset_include
        self.asset_exclude = asset_exclude
        self.enabled = enabled
        self.minimum_release_age = minimum_release_age
        self.cache_dir = cache_dir
        self.refresh_interval = refresh_interval
        self.arch = arch
        self.releasever = releasever
        self.github_token_file = github_token_file
        self.gpgcheck = gpgcheck

    @property
    def cache_path(self):
        return os.path.join(self.cache_dir, self.name)

    @property
    def owner_repo(self):
        return parse_github_url(self.url)


class PluginConfig:
    def __init__(self, path, main, repos, section_files=None):
        self.path = path
        self.main = main
        self.repos = repos
        self.section_files = section_files or {}


def validate_main_value(key: str, value: object) -> None:
    """Reject invalid main-section values before they are written to disk."""

    if key not in MAIN_CONFIG_KEYS:
        raise ConfigError(f"unknown main key: {key}")
    if key == "refresh_interval":
        parse_duration(value)
    elif key == "minimum_release_age":
        parse_duration(value)
    elif key == "debug":
        parse_bool(value)
    elif key == "asset_include":
        validate_asset_pattern(str(value), key=key)
    elif key == "asset_exclude":
        validate_asset_pattern(str(value), key=key)


def validate_repo_value(section: str, key: str, value: object) -> None:
    """Reject invalid repository values before they are written to disk."""

    if key not in REPO_CONFIG_KEYS:
        raise ConfigError(f"[{section}] unknown repository key: {key}")
    if key == "source":
        validate_source(str(value), section)
    elif key == "url":
        parse_github_url(str(value))
    elif key == "asset_include":
        validate_asset_pattern(str(value), section, key)
    elif key == "asset_exclude":
        validate_asset_pattern(str(value), section, key)
    elif key == "enabled":
        parse_bool(value)
    elif key == "gpgcheck":
        parse_bool(value)
    elif key == "minimum_release_age":
        parse_duration(value)
    elif key == "refresh_interval":
        parse_duration(value)


def validate_config_value(section: str, key: str, value: object) -> None:
    if section == "main":
        validate_main_value(key, value)
        return
    validate_repo_value(section, key, value)


def validate_repo_name(name: str) -> str:
    """Validate the INI section name used as the repository alias."""

    normalized = str(name).strip()
    if not normalized:
        raise ConfigError("repository name must not be empty")
    if normalized == "main":
        raise ConfigError("repository name must not be main")
    if any(char in normalized for char in "\r\n[]"):
        raise ConfigError(f"invalid repository name: {name}")
    return normalized


def warn_unknown_options(section: str, keys: Iterable[str], warn=None) -> None:
    """Ignore unknown config keys while surfacing them as warnings."""

    warn = warn or _default_config_warn
    allowed = MAIN_CONFIG_KEYS if section == "main" else REPO_CONFIG_KEYS
    for key in keys:
        if key in allowed:
            continue
        if section != "main" and key in LEGACY_REPO_KEYS:
            warn(
                f"ignoring legacy config key [{section}] {key}; "
                f"use {LEGACY_REPO_KEYS[key]} instead"
            )
            continue
        if section == "main":
            warn(f"ignoring unknown main key: {key}")
            continue
        warn(f"ignoring unknown repository key [{section}] {key}")


def _default_config_warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def default_include_path(path: str) -> str:
    """Derive the default include directory from the main config path."""

    directory = os.path.dirname(path)
    stem, _ = os.path.splitext(os.path.basename(path))
    return os.path.join(directory, f"{stem}.d")


def resolve_include_path(config_path: str, include_path: str) -> str:
    """Resolve relative include paths against the main config directory."""

    if os.path.isabs(include_path):
        return os.path.normpath(include_path)
    base_directory = os.path.dirname(config_path) or "."
    return os.path.normpath(os.path.join(base_directory, include_path))


def configured_include_path(
    path: str,
    parser: Optional[configparser.ConfigParser] = None,
    use_default_if_missing: bool = False,
) -> Optional[str]:
    """Return the include directory configured in [main]."""

    current = parser or read_parser(path, include=False, ensure_main=False)
    if current.has_section("main") and current.has_option("main", INCLUDE_KEY):
        include_path = current.get("main", INCLUDE_KEY).strip()
        if not include_path:
            return None
        return resolve_include_path(path, include_path)
    if use_default_if_missing:
        return default_include_path(path)
    return None


def ensure_main_defaults(parser: configparser.ConfigParser, path: str) -> None:
    """Keep the main file on the new include-based layout."""

    if not parser.has_section("main"):
        parser.add_section("main")
    if not parser.has_option("main", INCLUDE_KEY):
        parser.set("main", INCLUDE_KEY, default_include_path(path))


def read_parser(
    path: str = DEFAULT_CONFIG_PATH,
    include: bool = True,
    ensure_main: bool = True,
) -> configparser.ConfigParser:
    """Read the main config and optionally merge included repo files."""

    parser = configparser.ConfigParser()
    paths = [path]
    if include:
        base = configparser.ConfigParser()
        base.read(path)
        include_path = configured_include_path(path, base, use_default_if_missing=True)
        if include_path:
            paths.extend(sorted(glob.glob(os.path.join(include_path, "*.conf"))))
    parser.read(paths)
    if ensure_main and not parser.has_section("main"):
        parser.add_section("main")
    return parser


def _read_config_layers(path: str) -> List[Tuple[str, configparser.ConfigParser]]:
    """Read the base config plus each included repo file as separate layers."""

    layers = []
    base = read_parser(path, include=False, ensure_main=False)
    layers.append((path, base))
    include_path = configured_include_path(path, base, use_default_if_missing=True)
    if not include_path:
        return layers
    for include_file in sorted(glob.glob(os.path.join(include_path, "*.conf"))):
        parser = read_parser(include_file, include=False, ensure_main=False)
        layers.append((include_file, parser))
    return layers


def parse_duration(value: object) -> int:
    """Convert duration settings to seconds for refresh and release-age checks."""

    if isinstance(value, int):
        return value
    match = _DURATION_RE.match(str(value))
    if not match:
        raise ConfigError("duration must be an integer number of seconds or use s/m/h/d")
    amount = int(match.group(1))
    unit = match.group(2)
    multipliers = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
    return amount * multipliers[unit]


def format_duration(value: Optional[int], inherited: bool = False) -> str:
    formatted = _format_duration_value(value)
    if inherited:
        return f"global({formatted})"
    return formatted


def _format_duration_value(value: Optional[int]) -> str:
    seconds = int(value or 0)
    for suffix, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds and seconds % size == 0:
            return f"{seconds // size}{suffix}"
    return f"{seconds}s"


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "yes", "true", "on", "enabled"}:
        return True
    if normalized in {"0", "no", "false", "off", "disabled"}:
        return False
    raise ConfigError(f"invalid boolean value: {value}")


def current_arch() -> str:
    """Return the RPM architecture used for default GitHub asset filtering."""

    machine = platform.machine().lower()
    aliases = {
        "amd64": "x86_64",
        "arm64": "aarch64",
    }
    return aliases.get(machine, machine)


def current_releasever() -> Optional[str]:
    """Return the EL release marker used by RPM filenames, such as el9."""

    try:
        result = subprocess.run(
            ["rpm", "--eval", "%{?rhel}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
        )
        major = result.stdout.strip()
        if major.isdigit():
            return f"el{major}"
    except (OSError, ValueError):
        pass

    try:
        with open("/etc/os-release", "r", encoding="utf-8") as fh:
            values = {}
            for line in fh:
                key, sep, value = line.partition("=")
                if sep:
                    values[key] = value.strip().strip('"')
        major = values.get("VERSION_ID", "").split(".", 1)[0]
        if major.isdigit():
            return f"el{major}"
    except OSError:
        pass
    return None


def parse_github_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "github.com":
        raise ConfigError("url must be a GitHub repository URL")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise ConfigError("url must include GitHub owner and repository")
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return parts[0], repo


def validate_source(source: str, section: Optional[str] = None) -> str:
    if source != DEFAULT_SOURCE:
        prefix = f"[{section}] " if section else ""
        raise ConfigError(f"{prefix}unsupported source: {source}")
    return source


def validate_asset_pattern(
    pattern: str, section: Optional[str] = None, key: str = "asset pattern"
) -> str:
    try:
        re.compile(pattern)
    except re.error as exc:
        prefix = f"[{section}] " if section else ""
        raise ConfigError(f"{prefix}invalid {key}: {exc}") from exc
    return pattern


def repo_name_from_url(url: str) -> str:
    return parse_github_url(url)[1]


def load_config(path: str = DEFAULT_CONFIG_PATH, warn=None) -> PluginConfig:
    parser = read_parser(path)

    main_section = parser["main"] if parser.has_section("main") else {}
    warn_unknown_options("main", main_section.keys(), warn=warn)
    main = MainConfig(
        cache_dir=str(main_section.get("cache_dir", DEFAULT_CACHE_DIR)),
        refresh_interval=parse_duration(main_section.get("refresh_interval", DEFAULT_REFRESH_INTERVAL)),
        minimum_release_age=parse_duration(
            main_section.get("minimum_release_age", DEFAULT_MINIMUM_RELEASE_AGE)
        ),
        debug=parse_bool(main_section.get("debug", DEFAULT_DEBUG)),
        include=main_section.get(INCLUDE_KEY),
        asset_include=validate_asset_pattern(
            main_section.get("asset_include", DEFAULT_ASSET_INCLUDE),
            key="asset_include",
        ),
        asset_exclude=validate_asset_pattern(
            main_section.get("asset_exclude", DEFAULT_ASSET_EXCLUDE),
            key="asset_exclude",
        ),
    )

    repos: Dict[str, RepoConfig] = {}
    section_files: Dict[str, str] = {}
    for layer_path, layer_parser in _read_config_layers(path):
        for section in layer_parser.sections():
            if section == "main":
                continue
            section_files[section] = layer_path
    for section in parser.sections():
        if section == "main":
            continue
        item = parser[section]
        warn_unknown_options(section, item.keys(), warn=warn)
        source = item.get("source", DEFAULT_SOURCE)
        validate_source(source, section)
        if "url" not in item:
            raise ConfigError(f"[{section}] url is required")
        url = item["url"]
        parse_github_url(url)
        asset_include = validate_asset_pattern(
            item.get("asset_include", main.asset_include), section, "asset_include"
        )
        asset_exclude = validate_asset_pattern(
            item.get("asset_exclude", main.asset_exclude), section, "asset_exclude"
        )
        minimum_release_age = parse_duration(
            item.get("minimum_release_age", main.minimum_release_age)
        )
        cache_dir = item.get("cache_dir", main.cache_dir)
        refresh_interval = parse_duration(item.get("refresh_interval", main.refresh_interval))
        gpgcheck = parse_bool(item["gpgcheck"]) if "gpgcheck" in item else None
        repos[section] = RepoConfig(
            name=section,
            source=source,
            url=url,
            asset_include=asset_include,
            asset_exclude=asset_exclude,
            enabled=parse_bool(item.get("enabled", DEFAULT_ENABLED)),
            minimum_release_age=minimum_release_age,
            cache_dir=cache_dir,
            refresh_interval=refresh_interval,
            arch=item.get("arch") or current_arch(),
            releasever=item.get("releasever") or current_releasever(),
            github_token_file=item.get("github_token_file"),
            gpgcheck=gpgcheck,
        )
    return PluginConfig(path=path, main=main, repos=repos, section_files=section_files)


def write_parser(parser: configparser.ConfigParser, path: str = DEFAULT_CONFIG_PATH) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        parser.write(fh)


def repo_config_path(path: str, section: str) -> str:
    """Resolve which file stores the named repository section."""

    config = load_config(path)
    source_path = config.section_files.get(section)
    if source_path:
        return source_path
    include_path = configured_include_path(path, use_default_if_missing=True)
    if not include_path:
        return path
    return os.path.join(include_path, f"{section}.conf")


def section_options(path: str, section: str) -> Iterable[str]:
    """Return the explicitly configured keys for one section."""

    source_path = path if section == "main" else repo_config_path(path, section)
    parser = read_parser(source_path, include=False, ensure_main=False)
    if not parser.has_section(section):
        return set()
    return set(parser[section].keys())


def set_value(path: str, section: str, key: str, value: object) -> str:
    validate_config_value(section, key, value)
    target_path = path
    if section == "main":
        parser = read_parser(path, include=False)
        ensure_main_defaults(parser, path)
    else:
        target_path = repo_config_path(path, section)
        parser = read_parser(target_path, include=False, ensure_main=False)
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, key, str(value))
    write_parser(parser, target_path)
    return target_path


def unset_value(path: str, section: str, key: str) -> Tuple[bool, str]:
    target_path = path
    if section == "main":
        parser = read_parser(path, include=False)
        ensure_main_defaults(parser, path)
    else:
        target_path = repo_config_path(path, section)
        parser = read_parser(target_path, include=False, ensure_main=False)
    removed = parser.has_section(section) and parser.remove_option(section, key)
    write_parser(parser, target_path)
    return removed, target_path


def remove_section(path: str, section: str) -> Tuple[bool, str]:
    target_path = path if section == "main" else repo_config_path(path, section)
    parser = read_parser(
        target_path,
        include=False,
        ensure_main=(target_path == path),
    )
    if target_path == path:
        ensure_main_defaults(parser, path)
    removed = parser.remove_section(section)
    if target_path != path and removed and not parser.sections():
        if os.path.exists(target_path):
            os.unlink(target_path)
        return removed, target_path
    write_parser(parser, target_path)
    return removed, target_path


def add_repo(
    path: str,
    name: str,
    url: str,
    source: str = DEFAULT_SOURCE,
    values: Optional[Dict[str, object]] = None,
    force: bool = False,
) -> str:
    """Persist a repository section generated by the management CLI."""

    name = validate_repo_name(name)
    parse_github_url(url)
    validate_source(source)
    for key, value in (values or {}).items():
        if value is not None:
            validate_repo_value(name, key, value)
    main_parser = read_parser(path, include=False)
    ensure_main_defaults(main_parser, path)
    write_parser(main_parser, path)
    target_path = repo_config_path(path, name)
    parser = read_parser(target_path, include=False, ensure_main=False)
    if parser.has_section(name):
        if not force:
            raise ConfigError(f"repository already exists: {name}")
        parser.remove_section(name)
    parser.add_section(name)
    parser.set(name, "source", source)
    parser.set(name, "url", url)
    for key, value in (values or {}).items():
        if value is not None:
            parser.set(name, key, str(value))
    write_parser(parser, target_path)
    return target_path


def iter_repo_rows(config: PluginConfig) -> Iterable[RepoConfig]:
    return (config.repos[name] for name in sorted(config.repos))
