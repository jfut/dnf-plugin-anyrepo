# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

"""Command line management tool for AnyRepo-managed DNF repositories."""

import argparse
import configparser
import sys

from dnf_plugin_anyrepo import repo as local_repo
from dnf_plugin_anyrepo.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    add_repo,
    format_duration,
    iter_repo_rows,
    load_config,
    parse_github_url,
    remove_section,
    repo_name_from_url,
    set_value,
    unset_value,
    validate_repo_name,
)
from dnf_plugin_anyrepo.manager import RepositoryManager
from dnf_plugin_anyrepo.state import load_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dnf-anyrepo")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("add")
    add.add_argument("url")
    add.add_argument("-n", "--name")
    add.add_argument("--source", default="github-release")
    add.add_argument("--asset-regex")
    add.add_argument("--arch")
    add.add_argument("--releasever")
    add.add_argument("--minimum-release-age")
    add.add_argument("--github-token-file")
    state = add.add_mutually_exclusive_group()
    state.add_argument("--enabled", action="store_true")
    state.add_argument("--disabled", action="store_true")
    add.add_argument("--force", action="store_true")

    remove = sub.add_parser("remove")
    remove.add_argument("name")
    remove.add_argument("--purge-cache", action="store_true")

    sub.add_parser("list")

    refresh = sub.add_parser("refresh")
    refresh.add_argument("name", nargs="?")
    refresh.add_argument("--force", action="store_true")

    global_cmd = sub.add_parser("global")
    global_sub = global_cmd.add_subparsers(dest="global_command")
    global_sub.add_parser("show")
    get = global_sub.add_parser("get")
    get.add_argument("key")
    set_cmd = global_sub.add_parser("set")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    unset = global_sub.add_parser("unset")
    unset.add_argument("key")

    repo = sub.add_parser("repo")
    repo_sub = repo.add_subparsers(dest="repo_command")
    repo_show = repo_sub.add_parser("show")
    repo_show.add_argument("name")
    repo_set = repo_sub.add_parser("set")
    repo_set.add_argument("name")
    repo_set.add_argument("key")
    repo_set.add_argument("value")
    repo_unset = repo_sub.add_parser("unset")
    repo_unset.add_argument("name")
    repo_unset.add_argument("key")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.command:
        build_parser().print_help(sys.stderr)
        return 2
    if args.command == "global" and not args.global_command:
        print("dnf-anyrepo global: missing command", file=sys.stderr)
        return 2
    if args.command == "repo" and not args.repo_command:
        print("dnf-anyrepo repo: missing command", file=sys.stderr)
        return 2
    try:
        return _run(args)
    except (ConfigError, KeyError, RuntimeError, OSError) as exc:
        print(_format_cli_error(args, exc), file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    if args.command == "add":
        values = {
            "asset_regex": args.asset_regex,
            "arch": args.arch,
            "releasever": args.releasever,
            "minimum_release_age": args.minimum_release_age,
            "github_token_file": args.github_token_file,
        }
        if args.enabled:
            values["enabled"] = "true"
        if args.disabled:
            values["enabled"] = "false"
        name = validate_repo_name(args.name) if args.name else repo_name_from_url(args.url)
        add_repo(args.config, name, args.url, args.source, values, force=args.force)
        print(f"{args.config}: Added [{name}]")
        return 0

    if args.command == "remove":
        config = load_config(args.config)
        repo = config.repos.get(args.name)
        removed = remove_section(args.config, args.name)
        if args.purge_cache and repo:
            local_repo.remove_cache(repo.cache_path)
        if not removed:
            raise ConfigError(f"repository not found: {args.name}")
        # Match add output so users always see which config file changed.
        print(f"{args.config}: Removed [{args.name}]")
        return 0

    if args.command == "list":
        config = load_config(args.config)
        _print_list(config)
        return 0

    if args.command == "refresh":
        manager = RepositoryManager(config_path=args.config)
        if args.name:
            changed = manager.refresh(args.name, force=args.force)
            print(f"{args.name}: {'refreshed' if changed else 'unchanged'}")
        else:
            for name, changed in manager.refresh_all(force=args.force):
                print(f"{name}: {'refreshed' if changed else 'unchanged'}")
        return 0

    if args.command == "global":
        return _run_global_config(args)

    if args.command == "repo":
        if args.repo_command == "show":
            return _run_repo_show(args)
        if args.repo_command == "set":
            return _run_repo_set(args)
        if args.repo_command == "unset":
            return _run_repo_unset(args)
        raise ConfigError(f"unknown repo command: {args.repo_command}")

    raise ConfigError(f"unknown command: {args.command}")


def _run_repo_show(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    repo = config.repos[args.name]
    _print_repo_show(repo)
    return 0


def _run_repo_set(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.name not in config.repos:
        raise ConfigError(f"repository not found: {args.name}")
    before = _describe_current_value(args.config, args.name, args.key)
    set_value(args.config, args.name, args.key, args.value)
    print(f"{args.config}: [{args.name}] {args.key}: {before} -> {args.value}")
    return 0


def _run_repo_unset(args: argparse.Namespace) -> int:
    removed = unset_value(args.config, args.name, args.key)
    # Keep mutation output consistent with add/remove/set.
    verb = "Unset" if removed else "Not set"
    print(f"{args.config}: {verb} [{args.name}] {args.key}")
    return 0


def _run_global_config(args: argparse.Namespace) -> int:
    if args.global_command == "show":
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
        set_value(args.config, "main", args.key, args.value)
        print(f"{args.config}: [main] {args.key}: {before} -> {args.value}")
        return 0
    if args.global_command == "unset":
        removed = unset_value(args.config, "main", args.key)
        # Keep mutation output consistent with add/remove/set.
        verb = "Unset" if removed else "Not set"
        print(f"{args.config}: {verb} [main] {args.key}")
        return 0
    raise ConfigError(f"unknown global command: {args.global_command}")


def _format_cli_error(args: argparse.Namespace, exc: Exception) -> str:
    message = str(exc)

    # Normalize common repository errors so they match the config-prefixed style
    # used by successful mutating commands.
    if message.startswith("repository already exists: "):
        name = message.removeprefix("repository already exists: ")
        return f"{args.config}: Repository already exists [{name}]"
    if message.startswith("repository not found: "):
        name = message.removeprefix("repository not found: ")
        return f"{args.config}: Repository not found [{name}]"
    if message.startswith("unknown main key: "):
        key = message.removeprefix("unknown main key: ")
        return f"{args.config}: Unknown main key [{key}]"
    if "] unknown repository key: " in message:
        section, _, key = message.partition("] unknown repository key: ")
        name = section.removeprefix("[")
        return f"{args.config}: Unknown repository key [{name}] {key}"

    return f"dnf-anyrepo: {message}"


def _print_list(config) -> None:
    rows = [["NAME", "SOURCE", "URL", "ENABLED", "MIN_AGE"]]
    for repo in iter_repo_rows(config):
        inherited = "minimum_release_age" not in _section_options(config.path, repo.name)
        rows.append(
            [
                repo.name,
                repo.source,
                repo.url,
                "yes" if repo.enabled else "no",
                format_duration(repo.minimum_release_age, inherited=inherited),
            ]
        )
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _print_global_show(main) -> None:
    values = {
        "cache_dir": main.cache_dir,
        "refresh_interval": format_duration(main.refresh_interval),
        "minimum_release_age": format_duration(main.minimum_release_age),
        "debug": "true" if main.debug else "false",
    }
    for key, value in values.items():
        print(f"{key}: {value}")


def _print_repo_show(repo) -> None:
    owner, github_repo = parse_github_url(repo.url)
    state = load_state(repo.cache_path)
    cached_assets = state.get("asset_names", [])
    values = {
        "name": repo.name,
        "source": repo.source,
        "owner": owner,
        "repo": github_repo,
        "asset_regex": repo.asset_regex,
        "enabled": "yes" if repo.enabled else "no",
        "minimum_release_age": format_duration(repo.minimum_release_age),
        "cache_dir": repo.cache_path,
        "latest_tag": state.get("latest_tag", ""),
        "latest_release_id": state.get("latest_release_id", ""),
        "last_refresh_at": state.get("last_refresh_at", ""),
        "cached_assets": ", ".join(cached_assets),
    }
    for key, value in values.items():
        print(f"{key}: {value}")


def _section_options(path, section):
    parser = configparser.ConfigParser()
    parser.read(path)
    if not parser.has_section(section):
        return set()
    return set(parser[section].keys())


def _describe_current_value(path: str, section: str, key: str) -> str:
    if section == "main":
        config = load_config(path)
        if not hasattr(config.main, key):
            return "(unset)"
        return _format_display_value(key, getattr(config.main, key))

    parser = configparser.ConfigParser()
    parser.read(path)
    if parser.has_section(section) and parser.has_option(section, key):
        return str(parser.get(section, key))

    config = load_config(path)
    repo = config.repos.get(section)
    if repo is None or not hasattr(repo, key):
        return "(unset)"

    inherited = key not in _section_options(path, section)
    return _format_display_value(key, getattr(repo, key), inherited=inherited)


def _format_display_value(key: str, value, inherited: bool = False) -> str:
    if key in {"minimum_release_age", "refresh_interval"}:
        return format_duration(value, inherited=inherited)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
