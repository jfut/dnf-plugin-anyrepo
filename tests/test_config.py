# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from dnf_plugin_anyrepo.config import (
    ConfigError,
    DEFAULT_ASSET_INCLUDE,
    DEFAULT_ASSET_EXCLUDE,
    add_repo,
    default_include_path,
    load_config,
    parse_duration,
    parse_github_url,
    parse_priority,
    repo_name_from_url,
    set_value,
)


class ConfigTest(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(parse_duration("600"), 600)
        self.assertEqual(parse_duration("30m"), 1800)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("2d"), 172800)

    def test_parse_priority(self):
        self.assertEqual(parse_priority("10"), 10)
        self.assertEqual(parse_priority("0"), 0)
        self.assertEqual(parse_priority("100"), 100)
        with self.assertRaises(ConfigError):
            parse_priority("high")

    def test_parse_github_url(self):
        self.assertEqual(parse_github_url("https://github.com/jfut/prec"), ("jfut", "prec"))
        self.assertEqual(parse_github_url("https://github.com/jfut/prec.git"), ("jfut", "prec"))

    def test_load_repo_inherits_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    f"cache_dir = {tmp}\n"
                    "minimum_release_age = 1h\n"
                    "\n"
                    "[prec]\n"
                    "url = https://github.com/jfut/prec\n"
                )
            config = load_config(path)
            self.assertEqual(config.repos["prec"].minimum_release_age, 3600)
            self.assertEqual(config.repos["prec"].source, "github-release")
            self.assertEqual(config.repos["prec"].asset_include, DEFAULT_ASSET_INCLUDE)
            self.assertEqual(config.repos["prec"].asset_exclude, DEFAULT_ASSET_EXCLUDE)
            self.assertTrue(config.repos["prec"].enabled)
            self.assertIsNone(config.repos["prec"].gpgcheck)
            self.assertEqual(config.main.priority, 99)
            self.assertEqual(config.repos["prec"].priority, 99)
            self.assertFalse(config.main.debug)

    def test_load_repo_priority_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\npriority = 50\n\n"
                    "[prec]\nurl = https://github.com/jfut/prec\npriority = 10\n"
                )
            config = load_config(path)
            self.assertEqual(config.main.priority, 50)
            self.assertEqual(config.repos["prec"].priority, 10)

    def test_load_main_reads_asset_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    "asset_include = .*module.*\\.rpm$\n"
                    "asset_exclude = -tests-.*\\.rpm$\n"
                    "\n"
                    "[prec]\n"
                    "url = https://github.com/jfut/prec\n"
                )
            config = load_config(path)
            self.assertEqual(config.main.asset_include, ".*module.*\\.rpm$")
            self.assertEqual(config.main.asset_exclude, "-tests-.*\\.rpm$")
            self.assertEqual(config.repos["prec"].asset_include, ".*module.*\\.rpm$")
            self.assertEqual(config.repos["prec"].asset_exclude, "-tests-.*\\.rpm$")

    def test_load_repo_reads_asset_exclude(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[prec]\n"
                    "url = https://github.com/jfut/prec\n"
                    "asset_exclude = -tests-.*\\.rpm$\n"
                )
            config = load_config(path)
            self.assertEqual(config.repos["prec"].asset_exclude, "-tests-.*\\.rpm$")

    def test_load_repo_reads_gpgcheck(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[prec]\nurl = https://github.com/jfut/prec\ngpgcheck = 1\n")
            config = load_config(path)
            self.assertTrue(config.repos["prec"].gpgcheck)

    def test_load_main_debug(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[main]\ndebug = 1\n")
            config = load_config(path)
            self.assertTrue(config.main.debug)

    def test_add_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            target_path = add_repo(path, "prec", "https://github.com/jfut/prec")
            config = load_config(path)
            self.assertIn("prec", config.repos)
            self.assertEqual(target_path, os.path.join(tmp, "anyrepo.d", "prec.conf"))
            with open(path, "r", encoding="utf-8") as fh:
                self.assertIn(f"include = {default_include_path(path)}", fh.read())

    def test_repo_name_from_url_uses_repo_name(self):
        self.assertEqual(
            repo_name_from_url("https://github.com/jfut/prec"),
            "prec",
        )

    def test_load_repo_defaults_arch_to_current_arch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            add_repo(path, "prec", "https://github.com/jfut/prec")
            with mock.patch("dnf_plugin_anyrepo.config.current_arch", return_value="x86_64"):
                config = load_config(path)
            self.assertEqual(config.repos["prec"].arch, "x86_64")

    def test_load_repo_defaults_releasever_to_current_releasever(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            add_repo(path, "prec", "https://github.com/jfut/prec")
            with mock.patch("dnf_plugin_anyrepo.config.current_releasever", return_value="el9"):
                config = load_config(path)
            self.assertEqual(config.repos["prec"].releasever, "el9")

    def test_write_config_without_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                add_repo("anyrepo.conf", "prec", "https://github.com/jfut/prec")
                self.assertTrue(os.path.isfile("anyrepo.conf"))
                self.assertTrue(os.path.isfile(os.path.join("anyrepo.d", "prec.conf")))
            finally:
                os.chdir(cwd)

    def test_load_config_reads_repo_from_included_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            include_dir = os.path.join(tmp, "custom.d")
            os.makedirs(include_dir)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[main]\ninclude = custom.d\nminimum_release_age = 1h\n")
            with open(os.path.join(include_dir, "prec.conf"), "w", encoding="utf-8") as fh:
                fh.write("[prec]\nurl = https://github.com/jfut/prec\n")
            config = load_config(path)
            self.assertIn("prec", config.repos)
            self.assertEqual(config.repos["prec"].minimum_release_age, 3600)

    def test_load_config_uses_default_include_directory_when_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            include_dir = os.path.join(tmp, "anyrepo.d")
            os.makedirs(include_dir)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[main]\nminimum_release_age = 1h\n")
            with open(os.path.join(include_dir, "prec.conf"), "w", encoding="utf-8") as fh:
                fh.write("[prec]\nurl = https://github.com/jfut/prec\n")
            config = load_config(path)
            self.assertIn("prec", config.repos)
            self.assertEqual(config.repos["prec"].minimum_release_age, 3600)

    def test_rejects_unsupported_source_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with self.assertRaises(ConfigError):
                add_repo(path, "prec", "https://github.com/jfut/prec", source="bad")
            self.assertFalse(os.path.exists(path))

    def test_rejects_invalid_asset_include_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with self.assertRaises(ConfigError):
                add_repo(
                    path,
                    "prec",
                    "https://github.com/jfut/prec",
                    values={"asset_include": "["},
                )
            self.assertFalse(os.path.exists(path))

    def test_set_rejects_invalid_repo_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            add_repo(path, "prec", "https://github.com/jfut/prec")
            with self.assertRaises(ConfigError):
                set_value(path, "prec", "asset_include", "[")
            with self.assertRaises(ConfigError):
                set_value(path, "prec", "asset_exclude", "[")
            with self.assertRaises(ConfigError):
                set_value(path, "prec", "source", "bad")
            with self.assertRaises(ConfigError):
                set_value(path, "prec", "enabled", "maybe")
            with self.assertRaises(ConfigError):
                set_value(path, "prec", "gpgcheck", "maybe")
            with self.assertRaises(ConfigError):
                set_value(path, "prec", "priority", "high")
            with self.assertRaises(ConfigError):
                set_value(path, "prec", "refresh_interval", "soon")
            with self.assertRaises(ConfigError):
                set_value(path, "prec", "url", "https://example.com/jfut/prec")
            with self.assertRaises(ConfigError):
                set_value(path, "prec", "unknown_key", "value")

    def test_set_rejects_invalid_main_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with self.assertRaises(ConfigError):
                set_value(path, "main", "debug", "maybe")
            with self.assertRaises(ConfigError):
                set_value(path, "main", "asset_include", "[")
            with self.assertRaises(ConfigError):
                set_value(path, "main", "asset_exclude", "[")
            with self.assertRaises(ConfigError):
                set_value(path, "main", "minimum_release_age", "later")
            with self.assertRaises(ConfigError):
                set_value(path, "main", "priority", "high")
            with self.assertRaises(ConfigError):
                set_value(path, "main", "unknown_key", "value")

    def test_add_repo_rejects_invalid_enabled_value_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with self.assertRaises(ConfigError):
                add_repo(
                    path,
                    "prec",
                    "https://github.com/jfut/prec",
                    values={"enabled": "maybe"},
                )
            self.assertFalse(os.path.exists(path))

    def test_add_repo_rejects_invalid_alias_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with self.assertRaises(ConfigError):
                add_repo(path, "main", "https://github.com/jfut/prec")
            with self.assertRaises(ConfigError):
                add_repo(path, ".", "https://github.com/jfut/prec")
            with self.assertRaises(ConfigError):
                add_repo(path, "..", "https://github.com/jfut/prec")
            with self.assertRaises(ConfigError):
                add_repo(path, "bad[name]", "https://github.com/jfut/prec")
            with self.assertRaises(ConfigError):
                add_repo(path, "../outside", "https://github.com/jfut/prec")
            with self.assertRaises(ConfigError):
                add_repo(path, "nested/repo", "https://github.com/jfut/prec")
            self.assertFalse(os.path.exists(path))

    def test_load_config_rejects_unsafe_repo_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            for name in (".", "..", "../outside"):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(f"[{name}]\nurl = https://github.com/jfut/prec\n")
                with self.assertRaises(ConfigError):
                    load_config(path)

    def test_load_warns_and_ignores_unknown_main_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                # Catch typos that would otherwise make the intended setting inert.
                fh.write("[main]\nminimum_release_gae = 1h\n")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                config = load_config(path)
            self.assertEqual(config.main.minimum_release_age, 3 * 86400)
            self.assertEqual(
                stderr.getvalue().strip(),
                "warning: ignoring unknown main key: minimum_release_gae",
            )

    def test_load_warns_and_ignores_unknown_repo_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                # Catch typos that would otherwise leave the repository enabled.
                fh.write("[prec]\nurl = https://github.com/jfut/prec\nenabledd = false\n")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                config = load_config(path)
            self.assertIn("prec", config.repos)
            self.assertTrue(config.repos["prec"].enabled)
            self.assertEqual(
                stderr.getvalue().strip(),
                "warning: ignoring unknown repository key [prec] enabledd",
            )

    def test_load_warns_and_ignores_legacy_repo_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[prec]\nurl = https://github.com/jfut/prec\nasset_regex = .*\\.rpm$\n")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                config = load_config(path)
            self.assertEqual(config.repos["prec"].asset_include, DEFAULT_ASSET_INCLUDE)
            self.assertEqual(
                stderr.getvalue().strip(),
                "warning: ignoring legacy config key [prec] asset_regex; use asset_include instead",
            )


if __name__ == "__main__":
    unittest.main()
