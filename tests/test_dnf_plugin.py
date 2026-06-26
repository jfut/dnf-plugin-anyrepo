# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

import os
import tempfile
import unittest
from io import StringIO
from unittest import mock

from dnf_plugin_anyrepo.config import ConfigError, RepoConfig
from dnf_plugin_anyrepo.dnf_plugin import (
    AnyRepoPlugin,
    anyrepo_cache_dirs,
    clear_anyrepo_cache_dirs,
    effective_repo_gpgcheck,
    github_repo_id,
    repo_switch_enabled,
    repo_switch_gpgcheck,
)


try:
    import dnf
except ImportError:
    dnf = None


class DnfPluginTest(unittest.TestCase):
    def test_github_repo_id_uses_github_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = RepoConfig(
                name="prec",
                source="github-release",
                url="https://github.com/jfut/prec",
                asset_include=r".*\.rpm$",
                enabled=True,
                minimum_release_age=0,
                cache_dir=tmp,
                refresh_interval=600,
            )
            self.assertEqual(github_repo_id(repo), "github.com:jfut:prec")

    def test_repo_switch_defaults_to_enabled_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(repo_switch_enabled(os.path.join(tmp, "missing.repo")))

    def test_repo_switch_reads_enabled_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.repo")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[anyrepo]\nenabled = 0\n")
            self.assertFalse(repo_switch_enabled(path))

    def test_repo_switch_rejects_invalid_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.repo")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[anyrepo]\nenabled = maybe\n")
            with self.assertRaises(ValueError):
                repo_switch_enabled(path)

    def test_repo_switch_gpgcheck_defaults_to_disabled_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(repo_switch_gpgcheck(os.path.join(tmp, "missing.repo")))

    def test_repo_switch_reads_gpgcheck_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.repo")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[anyrepo]\ngpgcheck = 1\n")
            self.assertTrue(repo_switch_gpgcheck(path))

    def test_effective_repo_gpgcheck_inherits_when_unset(self):
        repo = RepoConfig(
            name="prec",
            source="github-release",
            url="https://github.com/jfut/prec",
            asset_include=r".*\.rpm$",
            enabled=True,
            minimum_release_age=0,
            cache_dir="/tmp",
            refresh_interval=600,
        )
        self.assertTrue(effective_repo_gpgcheck(repo, True))

    def test_effective_repo_gpgcheck_prefers_repo_value(self):
        repo = RepoConfig(
            name="prec",
            source="github-release",
            url="https://github.com/jfut/prec",
            asset_include=r".*\.rpm$",
            enabled=True,
            minimum_release_age=0,
            cache_dir="/tmp",
            refresh_interval=600,
            gpgcheck=False,
        )
        self.assertFalse(effective_repo_gpgcheck(repo, True))

    def test_anyrepo_cache_dirs_collects_main_and_repo_cache_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "anyrepo.conf")
            with open(config_path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    f"cache_dir = {os.path.join(tmp, 'main-cache')}\n"
                    "\n"
                    "[prec]\n"
                    "url = https://github.com/jfut/prec\n"
                    f"cache_dir = {os.path.join(tmp, 'repo-cache')}\n"
                )
            self.assertEqual(
                anyrepo_cache_dirs(config_path),
                sorted(
                    [
                        os.path.abspath("/var/cache/dnf/anyrepo"),
                        os.path.abspath(os.path.join(tmp, "main-cache")),
                        os.path.abspath(os.path.join(tmp, "repo-cache")),
                    ]
                ),
            )

    def test_clear_anyrepo_cache_dirs_removes_contents_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = os.path.join(tmp, "anyrepo")
            nested_dir = os.path.join(cache_dir, "repo", "repodata")
            os.makedirs(nested_dir)
            with open(os.path.join(cache_dir, "state.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")
            with open(os.path.join(nested_dir, "primary.xml"), "w", encoding="utf-8") as fh:
                fh.write("data")

            removed = clear_anyrepo_cache_dirs([cache_dir])

            self.assertEqual(removed, 2)
            self.assertTrue(os.path.isdir(cache_dir))
            self.assertEqual(os.listdir(cache_dir), [])

    @unittest.skipIf(dnf is None, "dnf is not available")
    def test_debug_output_is_suppressed_by_default(self):
        base = dnf.Base()
        plugin = AnyRepoPlugin(base, None)
        plugin.logger = mock.Mock()
        plugin._debug("checked GitHub repository prec")
        plugin.logger.debug.assert_not_called()

    @unittest.skipIf(dnf is None, "dnf is not available")
    def test_debug_output_is_enabled_by_config_flag(self):
        base = dnf.Base()
        plugin = AnyRepoPlugin(base, None)
        plugin.logger = mock.Mock()
        plugin._anyrepo_debug = True
        plugin._debug("checked GitHub repository prec")
        plugin.logger.debug.assert_called_once_with("checked GitHub repository prec")

    @unittest.skipIf(dnf is None, "dnf is not available")
    def test_config_error_warns_and_disables_anyrepo(self):
        base = dnf.Base()
        plugin = AnyRepoPlugin(base, None)
        plugin.logger = mock.Mock()

        with mock.patch(
            "dnf_plugin_anyrepo.dnf_plugin.RepositoryManager",
            side_effect=ConfigError("unknown main key: typo"),
        ):
            plugin.config()

        plugin.logger.warning.assert_called_once_with(
            "failed to load AnyRepo configuration: unknown main key: typo"
        )
        self.assertEqual(plugin._anyrepo_repo_ids, set())
        self.assertEqual(plugin._anyrepo_repo_names, {})

    @unittest.skipIf(dnf is None, "dnf is not available")
    def test_added_repo_inherits_gpgcheck_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = RepoConfig(
                name="prec",
                source="github-release",
                url="https://github.com/jfut/prec",
                asset_include=r".*\.rpm$",
                enabled=True,
                minimum_release_age=0,
                cache_dir=tmp,
                refresh_interval=600,
            )
            base = dnf.Base()
            plugin = AnyRepoPlugin(base, None)
            plugin._anyrepo_gpgcheck = True
            plugin._add_file_repo(repo)
            dnf_repo = base.repos[github_repo_id(repo)]
            self.assertTrue(dnf_repo.gpgcheck)
            self.assertFalse(dnf_repo.repo_gpgcheck)

    @unittest.skipIf(dnf is None, "dnf is not available")
    def test_warn_unsigned_packages_continues_with_assumeyes(self):
        base = dnf.Base()
        base.conf.assumeyes = True
        plugin = AnyRepoPlugin(base, None)
        pkg = mock.Mock()
        pkg.name = "prec"

        with mock.patch("sys.stderr", new=StringIO()) as stderr:
            plugin._warn_unsigned_packages([pkg])

        self.assertEqual(
            stderr.getvalue(),
            "\nWARNING: To continue installing unsigned AnyRepo packages, "
            "configure the following:\n"
            "- dnf-anyrepo repo prec set gpgcheck 0\n\n",
        )

    @unittest.skipIf(dnf is None, "dnf is not available")
    def test_warn_unsigned_packages_lists_each_package(self):
        base = dnf.Base()
        base.conf.assumeyes = False
        plugin = AnyRepoPlugin(base, None)
        pkg = mock.Mock()
        pkg.name = "prec"
        pkg2 = mock.Mock()
        pkg2.name = "sslcert-cli"

        with mock.patch("sys.stderr", new=StringIO()) as stderr:
            plugin._warn_unsigned_packages([pkg, pkg2])

        self.assertEqual(
            stderr.getvalue(),
            "\nWARNING: To continue installing unsigned AnyRepo packages, "
            "configure the following:\n"
            "- dnf-anyrepo repo prec set gpgcheck 0\n"
            "- dnf-anyrepo repo sslcert-cli set gpgcheck 0\n\n",
        )

    @unittest.skipIf(dnf is None, "dnf is not available")
    def test_warn_unsigned_packages_uses_repo_aliases(self):
        base = dnf.Base()
        plugin = AnyRepoPlugin(base, None)
        plugin._anyrepo_repo_names = {"github.com:jfut:nmcli-cli": "nmcli"}
        pkg = mock.Mock()
        pkg.name = "nmcli-cli"
        pkg.repoid = "github.com:jfut:nmcli-cli"

        with mock.patch("sys.stderr", new=StringIO()) as stderr:
            plugin._warn_unsigned_packages([pkg])

        self.assertEqual(
            stderr.getvalue(),
            "\nWARNING: To continue installing unsigned AnyRepo packages, "
            "configure the following:\n"
            "- dnf-anyrepo repo nmcli set gpgcheck 0\n\n",
        )


if __name__ == "__main__":
    unittest.main()
