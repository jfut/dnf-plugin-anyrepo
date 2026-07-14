# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

"""Command line management tool for AnyRepo-managed DNF repositories."""

import argparse
import configparser
import os
import sys

from dnf_plugin_anyrepo import __commit__, __version__
from dnf_plugin_anyrepo import repo as local_repo
from dnf_plugin_anyrepo.config import (
    DEFAULT_CACHE_DIR,
    DEFAULT_CONFIG_PATH,
    ConfigError,
    RepoConfig,
    add_repo,
    current_arch,
    current_releasever,
    format_duration,
    iter_repo_rows,
    load_config,
    parse_duration,
    parse_priority,
    parse_github_url,
    remove_section,
    repo_name_from_url,
    repo_config_path,
    section_options,
    set_value,
    unset_value,
    validate_repo_name,
    validate_repo_value,
    validate_source,
)
from dnf_plugin_anyrepo.dnf_plugin import (
    github_debug_repo_id,
    github_repo_id,
    github_source_repo_id,
    repo_switch_gpgcheck,
)
from dnf_plugin_anyrepo.manager import RepositoryManager, provider_for

GLOBAL_REFRESH_KEYS = {
    "asset_exclude",
    "asset_include",
    "minimum_release_age",
}
REPO_REFRESH_KEYS = {
    "asset_exclude",
    "asset_include",
    "arch",
    "github_token_file",
    "minimum_release_age",
    "releasever",
    "source",
    "url",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dnf-anyrepo")
    # Print the installed package version before parsing subcommands.
    parser.add_argument("-v", "--version", action="version", version=_cli_version())
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("add")
    add.add_argument("url")
    add.add_argument("-n", "--name")
    add.add_argument("--source", default="github-release")
    add.add_argument("--asset-include")
    add.add_argument("--asset-exclude")
    add.add_argument("--arch")
    add.add_argument("--releasever")
    add.add_argument("--minimum-release-age")
    add.add_argument("--priority")
    add.add_argument("--github-token-file")
    state = add.add_mutually_exclusive_group()
    state.add_argument("--enabled", action="store_true")
    state.add_argument("--disabled", action="store_true")
    add.add_argument("--force", action="store_true")

    remove = sub.add_parser("remove")
    remove.add_argument("name")
    remove.add_argument("-p", "--purge-cache", action="store_true")

    sub.add_parser("list")

    refresh = sub.add_parser("refresh")
    refresh.add_argument("name", nargs="?")
    refresh.add_argument("-f", "--force", action="store_true")

    global_cmd = sub.add_parser("global")
    global_sub = global_cmd.add_subparsers(dest="global_command")
    get = global_sub.add_parser("get")
    get.add_argument("key")
    set_cmd = global_sub.add_parser("set")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    unset = global_sub.add_parser("unset")
    unset.add_argument("key")

    repo = sub.add_parser("repo")
    repo.add_argument("name")
    repo_sub = repo.add_subparsers(dest="repo_command")
    repo_set = repo_sub.add_parser("set")
    repo_set.add_argument("key")
    repo_set.add_argument("value")
    repo_unset = repo_sub.add_parser("unset")
    repo_unset.add_argument("key")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.command:
        build_parser().print_help(sys.stderr)
        return 2
    try:
        return _run(args)
    except (ConfigError, RuntimeError, OSError) as exc:
        print(_format_cli_error(args, exc), file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    if args.command == "add":
        values = {
            "asset_include": args.asset_include,
            "asset_exclude": args.asset_exclude,
            "arch": args.arch,
            "releasever": args.releasever,
            "minimum_release_age": args.minimum_release_age,
            "priority": args.priority,
            "github_token_file": args.github_token_file,
        }
        if args.enabled:
            values["enabled"] = "true"
        if args.disabled:
            values["enabled"] = "false"
        owner, repository = parse_github_url(args.url)
        url = f"https://github.com/{owner}/{repository}"
        name = validate_repo_name(args.name) if args.name else repo_name_from_url(url)
        validate_repository(args.config, name, url, args.source, values)
        target_path = add_repo(args.config, name, url, args.source, values, force=args.force)
        _print_mutation_result(target_path, f"[{name}] repo added")
        return 0

    if args.command == "remove":
        cache_path = _repo_cache_path_for_remove(args.config, args.name) if args.purge_cache else None
        removed, target_path = remove_section(args.config, args.name)
        if not removed:
            raise ConfigError(f"repository not found: {args.name}")
        if cache_path:
            local_repo.remove_cache(cache_path)
        _print_mutation_result(target_path, f"[{args.name}] repo removed")
        return 0

    if args.command == "list":
        config = load_config(args.config)
        _print_list(config)
        return 0

    if args.command == "refresh":
        manager = RepositoryManager(config_path=args.config)
        if args.name:
            changed = manager.refresh(args.name, force=args.force)
            _clear_dnf_metadata_cache(manager.repo(args.name))
            # Keep refresh output aligned with other CLI messages and include config context.
            _print_refresh_result(args.config, args.name, changed)
        else:
            for name, changed in manager.refresh_all(force=args.force):
                _clear_dnf_metadata_cache(manager.repo(name))
                # Reuse the same display format for batch refresh output.
                _print_refresh_result(args.config, name, changed)
        return 0

    if args.command == "global":
        return _run_global_config(args)

    if args.command == "repo":
        if not args.repo_command:
            return _run_repo_show(args)
        if args.repo_command == "set":
            return _run_repo_set(args)
        if args.repo_command == "unset":
            return _run_repo_unset(args)
        raise ConfigError(f"unknown repo command: {args.repo_command}")

    raise ConfigError(f"unknown command: {args.command}")


def validate_repository(path: str, name: str, url: str, source: str, values: dict) -> None:
    """Verify a candidate repository before persisting its configuration."""

    validate_source(source)
    validate_repo_value(name, "url", url)
    for key, value in values.items():
        if value is not None:
            validate_repo_value(name, key, value)

    main = load_config(path).main
    candidate = RepoConfig(
        name=name,
        source=source,
        url=url,
        asset_include=values["asset_include"] or main.asset_include,
        asset_exclude=values["asset_exclude"] or main.asset_exclude,
        enabled=True,
        minimum_release_age=parse_duration(
            values["minimum_release_age"] or main.minimum_release_age
        ),
        priority=parse_priority(values["priority"] or main.priority),
        cache_dir=main.cache_dir or DEFAULT_CACHE_DIR,
        refresh_interval=main.refresh_interval,
        arch=values["arch"] or current_arch(),
        releasever=values["releasever"] or current_releasever(),
        github_token_file=values["github_token_file"],
    )
    # Providers perform a read-only API check and do not create cache or config files.
    provider_for(candidate).validate()


def _cli_version() -> str:
    version = "dev" if __version__ == "0.dev0" else __version__
    return f"dnf-anyrepo {version} ({__commit__})"


def _run_repo_show(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    repo = _require_repo(config, args.name)
    _print_repo_show(config, repo)
    return 0


def _repo_cache_path_for_remove(path: str, repo_name: str) -> str:
    # Allow stale sections to be removed without validating unrelated legacy keys.
    parser = configparser.ConfigParser()
    parser.read(path)
    if not parser.has_section(repo_name):
        return ""
    main_cache_dir = parser.get("main", "cache_dir", fallback=DEFAULT_CACHE_DIR)
    repo_cache_dir = parser.get(repo_name, "cache_dir", fallback=main_cache_dir)
    return os.path.join(repo_cache_dir, repo_name)


def _clear_dnf_metadata_cache(repo) -> None:
    # Drop DNF's cached metadata so the next transaction sees the refreshed AnyRepo contents.
    local_repo.clear_dnf_repo_metadata(
        [
            github_repo_id(repo),
            github_debug_repo_id(repo),
            github_source_repo_id(repo),
        ]
    )


def _run_repo_set(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    _require_repo(config, args.name)
    before = _describe_current_value(args.config, args.name, args.key)
    target_path = set_value(args.config, args.name, args.key, args.value)
    _print_set_result(target_path, args.name, args.key, before, args.value)
    _print_repo_refresh_hint(args.name, args.key)
    return 0


def _run_repo_unset(args: argparse.Namespace) -> int:
    removed, target_path = unset_value(args.config, args.name, args.key)
    verb = "unset" if removed else "not set"
    _print_mutation_result(target_path, f"[{args.name}] {args.key} {verb}")
    _print_repo_refresh_hint(args.name, args.key)
    return 0


def _run_global_config(args: argparse.Namespace) -> int:
    if not args.global_command:
        config = load_config(args.config)
        _print_global_show(config.main)
        return 0
    if args.global_command == "get":
        config = load_config(args.config)
        if not hasattr(config.main, args.key):
            raise ConfigError(f"unknown main key: {args.key}")
        print(getattr(config.main, args.key))
        return 0
    if args.global_command == "set":
        before = _describe_current_value(args.config, "main", args.key)
        target_path = set_value(args.config, "main", args.key, args.value)
        _print_set_result(target_path, "main", args.key, before, args.value)
        _print_global_refresh_hint(args.key)
        return 0
    if args.global_command == "unset":
        removed, target_path = unset_value(args.config, "main", args.key)
        verb = "unset" if removed else "not set"
        _print_mutation_result(target_path, f"[main] {args.key} {verb}")
        _print_global_refresh_hint(args.key)
        return 0
    raise ConfigError(f"unknown global command: {args.global_command}")


def _format_cli_error(args: argparse.Namespace, exc: Exception) -> str:
    message = str(exc)

    # Normalize common config errors so the changed item stays easy to scan.
    if message.startswith("repository already exists: "):
        name = _remove_prefix(message, "repository already exists: ")
        return _format_mutation_result(repo_config_path(args.config, name), f"[{name}] repo already exists")
    if message.startswith("repository not found: "):
        name = _remove_prefix(message, "repository not found: ")
        return _format_mutation_result(args.config, f"[{name}] repo not found")
    if message.startswith("unknown main key: "):
        key = _remove_prefix(message, "unknown main key: ")
        return _format_mutation_result(args.config, f"[main] unknown key {key}")
    if "] unknown repository key: " in message:
        section, _, key = message.partition("] unknown repository key: ")
        name = _remove_prefix(section, "[")
        return _format_mutation_result(args.config, f"[{name}] unknown repo key {key}")
    if ": " in message:
        name, detail = message.split(": ", 1)
        if name and "[" not in name and "]" not in name:
            return f"ERROR: [{name}]: {detail}"

    return f"dnf-anyrepo: {message}"


def _remove_prefix(value: str, prefix: str) -> str:
    # Keep Python 3.6 compatibility while mirroring str.removeprefix behavior.
    if value.startswith(prefix):
        return value[len(prefix) :]
    return value


def _require_repo(config, name: str):
    if name not in config.repos:
        raise ConfigError(f"repository not found: {name}")
    return config.repos[name]


def _print_list(config) -> None:
    rows = [["NAME", "SOURCE", "URL", "ENABLED", "GPGCHECK", "MIN_AGE"]]
    for repo in iter_repo_rows(config):
        inherited = "minimum_release_age" not in _section_options(config.path, repo.name)
        rows.append(
            [
                repo.name,
                repo.source,
                repo.url,
                "yes" if repo.enabled else "no",
                _format_repo_config_value(config, repo, "gpgcheck"),
                format_duration(repo.minimum_release_age, inherited=inherited),
            ]
        )
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _print_global_show(main) -> None:
    values = {
        "asset_exclude": main.asset_exclude,
        "asset_include": main.asset_include,
        "cache_dir": main.cache_dir,
        "debug": "true" if main.debug else "false",
        "refresh_interval": format_duration(main.refresh_interval),
        "minimum_release_age": format_duration(main.minimum_release_age),
        "priority": str(main.priority),
    }
    for key, value in sorted(values.items()):
        _print_key_value(key, value)


def _print_repo_show(config, repo) -> None:
    values = {
        "asset_include": _format_repo_config_value(config, repo, "asset_include"),
        "arch": _format_repo_config_value(config, repo, "arch"),
        "cache_dir": _format_repo_config_value(config, repo, "cache_dir"),
        "enabled": _format_repo_config_value(config, repo, "enabled"),
        "asset_exclude": _format_repo_config_value(config, repo, "asset_exclude"),
        "github_token_file": _format_repo_config_value(config, repo, "github_token_file"),
        "gpgcheck": _format_repo_config_value(config, repo, "gpgcheck"),
        "minimum_release_age": _format_repo_config_value(config, repo, "minimum_release_age"),
        "priority": _format_repo_config_value(config, repo, "priority"),
        "refresh_interval": _format_repo_config_value(config, repo, "refresh_interval"),
        "releasever": _format_repo_config_value(config, repo, "releasever"),
        "source": _format_repo_config_value(config, repo, "source"),
        "url": _format_repo_config_value(config, repo, "url"),
    }
    for key, value in sorted(values.items()):
        _print_key_value(key, value)


def _print_key_value(key: str, value: str) -> None:
    # Avoid trailing whitespace when an optional config value is unset.
    if value == "":
        print(f"{key}:")
        return
    print(f"{key}: {value}")


def _print_set_result(path: str, section: str, key: str, before: str, after: str) -> None:
    # Show the changed setting first so the important diff is easy to scan.
    _print_mutation_result(path, f"[{section}] {key}: {before} -> {after}")


def _print_refresh_result(path: str, name: str, changed: bool) -> None:
    # Report refresh status in the same bracketed format used by config mutations.
    status = "refreshed" if changed else "unchanged"
    _print_mutation_result(path, f"[{name}] {status}")


def _print_mutation_result(path: str, message: str) -> None:
    # Keep the changed item first and leave the source config path as context.
    print(_format_mutation_result(path, message))


def _format_mutation_result(path: str, message: str) -> str:
    return f"{message} ({path})"


def _format_repo_config_value(config, repo, key: str) -> str:
    value = getattr(repo, key)
    if key == "gpgcheck" and key not in _section_options(config.path, repo.name):
        return _format_inherited_gpgcheck()
    inherited_global_keys = {
        "asset_exclude",
        "asset_include",
        "cache_dir",
        "refresh_interval",
        "minimum_release_age",
        "priority",
    }
    if key in inherited_global_keys and key not in _section_options(config.path, repo.name):
        return f"global({_format_display_value(key, value)})"
    return _format_display_value(key, value)


def _section_options(path, section):
    return set(section_options(path, section))


def _describe_current_value(path: str, section: str, key: str) -> str:
    config = load_config(path)

    if section == "main":
        if not hasattr(config.main, key):
            return "(unset)"
        return _format_display_value(key, getattr(config.main, key))

    explicit_options = _section_options(path, section)
    if key in explicit_options:
        repo = config.repos.get(section)
        if repo is None or not hasattr(repo, key):
            return "(unset)"
        return _format_display_value(key, getattr(repo, key))

    repo = config.repos.get(section)
    if repo is None or not hasattr(repo, key):
        return "(unset)"

    inherited = key not in explicit_options
    if key == "gpgcheck" and inherited:
        return _format_inherited_gpgcheck()
    inherited_global_keys = {
        "asset_exclude",
        "asset_include",
        "cache_dir",
        "refresh_interval",
        "minimum_release_age",
        "priority",
    }
    if inherited and key in inherited_global_keys:
        return f"global({_format_display_value(key, getattr(repo, key))})"
    return _format_display_value(key, getattr(repo, key), inherited=inherited)


def _format_display_value(key: str, value, inherited: bool = False) -> str:
    if value is None:
        return ""
    if key in {"minimum_release_age", "refresh_interval"}:
        return format_duration(value, inherited=inherited)
    if key == "gpgcheck":
        return "1" if value else "0"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _format_inherited_gpgcheck() -> str:
    # gpgcheck is inherited from the user-facing anyrepo.repo DNF switch.
    return f"global({1 if repo_switch_gpgcheck() else 0})"


def _print_repo_refresh_hint(name: str, key: str) -> None:
    # Explain the skipped auto-refresh, then print the exact follow-up command.
    if key not in REPO_REFRESH_KEYS:
        return
    print()
    print("NOTICE: Run refresh immediately to apply the configuration changes.")
    print(f"-> dnf-anyrepo refresh {name} -f")


def _print_global_refresh_hint(key: str) -> None:
    # Explain the skipped auto-refresh, then print the exact follow-up command.
    if key not in GLOBAL_REFRESH_KEYS:
        return
    print()
    print("NOTICE: Run refresh immediately to apply the configuration changes.")
    print("-> dnf-anyrepo refresh -f")


if __name__ == "__main__":
    raise SystemExit(main())
