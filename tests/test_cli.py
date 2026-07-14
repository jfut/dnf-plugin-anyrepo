# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from dnf_plugin_anyrepo.cli import main
from dnf_plugin_anyrepo.config import RepoConfig, load_config


SSL_CERT_REPO = "sslcert-cli"


class CliTest(unittest.TestCase):
    def setUp(self):
        # Most CLI tests focus on configuration behavior, not live GitHub validation.
        self.validate_repository = mock.patch(
            "dnf_plugin_anyrepo.cli.validate_repository"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def _repo_path(self, directory: str, name: str) -> str:
        # Repo-specific commands now operate on one file per repository.
        return os.path.join(directory, "anyrepo.d", f"{name}.conf")

    def test_add_prints_config_path_after_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            repo_path = self._repo_path(tmp, SSL_CERT_REPO)
            stdout = io.StringIO()
            # Show the added repository first, then the config file that changed.
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "add", "https://github.com/jfut/sslcert-cli"])
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), f"[{SSL_CERT_REPO}] repo added ({repo_path})")
            self.assertTrue(os.path.isfile(repo_path))

    def test_add_normalizes_github_url_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            repo_path = self._repo_path(tmp, "prec")
            with contextlib.redirect_stdout(io.StringIO()):
                result = main(
                    [
                        "--config",
                        path,
                        "add",
                        "https://github.com/jfut/prec/aaa/bbb/ccc",
                    ]
                )
            self.assertEqual(result, 0)
            with open(repo_path, encoding="utf-8") as fh:
                self.assertIn("url = https://github.com/jfut/prec\n", fh.read())

    def test_add_rejects_repository_without_compatible_release_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            error = RuntimeError("dummyrepo: GitHub API returned HTTP 404: Not Found")
            stderr = io.StringIO()
            with mock.patch(
                "dnf_plugin_anyrepo.cli.validate_repository", side_effect=error
            ), contextlib.redirect_stderr(stderr):
                result = main(
                    ["--config", path, "add", "https://github.com/dummy/dummyrepo"]
                )
            self.assertEqual(result, 1)
            self.assertEqual(
                stderr.getvalue().strip(),
                "ERROR: [dummyrepo]: GitHub API returned HTTP 404: Not Found",
            )
            self.assertFalse(os.path.exists(path))
            self.assertFalse(os.path.exists(self._repo_path(tmp, "dummyrepo")))

    def test_version_flag_prints_installed_rpm_version(self):
        stdout = io.StringIO()
        with mock.patch(
            "dnf_plugin_anyrepo.cli.__version__",
            "1.3.1",
        ), mock.patch(
            "dnf_plugin_anyrepo.cli.__commit__",
            "71d03167cf0cae4d8408970851fa62be197d68ea",
        ):
            with self.assertRaises(SystemExit) as exc, contextlib.redirect_stdout(stdout):
                main(["--version"])
        self.assertEqual(exc.exception.code, 0)
        self.assertEqual(
            stdout.getvalue().strip(),
            "dnf-anyrepo 1.3.1 (71d03167cf0cae4d8408970851fa62be197d68ea)",
        )

    def test_short_version_flag_prints_installed_rpm_version(self):
        stdout = io.StringIO()
        with mock.patch(
            "dnf_plugin_anyrepo.cli.__version__",
            "1.3.1",
        ), mock.patch(
            "dnf_plugin_anyrepo.cli.__commit__",
            "71d03167cf0cae4d8408970851fa62be197d68ea",
        ):
            with self.assertRaises(SystemExit) as exc, contextlib.redirect_stdout(stdout):
                main(["-v"])
        self.assertEqual(exc.exception.code, 0)
        self.assertEqual(
            stdout.getvalue().strip(),
            "dnf-anyrepo 1.3.1 (71d03167cf0cae4d8408970851fa62be197d68ea)",
        )

    def test_version_flag_uses_default_build_metadata(self):
        stdout = io.StringIO()
        with self.assertRaises(SystemExit) as exc, contextlib.redirect_stdout(stdout):
            main(["--version"])
        self.assertEqual(exc.exception.code, 0)
        self.assertEqual(stdout.getvalue().strip(), "dnf-anyrepo dev (none)")

    def test_add_existing_repo_prints_config_path_after_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            repo_path = self._repo_path(tmp, SSL_CERT_REPO)
            with contextlib.redirect_stdout(io.StringIO()):
                main(["--config", path, "add", "https://github.com/jfut/sslcert-cli"])
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(["--config", path, "add", "https://github.com/jfut/sslcert-cli"])
            self.assertEqual(result, 1)
            self.assertEqual(
                stderr.getvalue().strip(),
                f"[{SSL_CERT_REPO}] repo already exists ({repo_path})",
            )

    def test_remove_prints_config_path_after_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            repo_path = self._repo_path(tmp, SSL_CERT_REPO)
            os.makedirs(os.path.dirname(repo_path))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"[main]\ninclude = {os.path.join(tmp, 'anyrepo.d')}\n")
            with open(repo_path, "w", encoding="utf-8") as fh:
                fh.write(f"[{SSL_CERT_REPO}]\nurl = https://github.com/jfut/sslcert-cli\n")
            stdout = io.StringIO()
            # Show the removed repository first, then the config file that changed.
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "remove", SSL_CERT_REPO])
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), f"[{SSL_CERT_REPO}] repo removed ({repo_path})")
            self.assertFalse(os.path.exists(repo_path))

    def test_remove_ignores_unknown_legacy_repo_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    f"[{SSL_CERT_REPO}]\n"
                    "url = https://github.com/jfut/sslcert-cli\n"
                    "asset_regex = .*\\.rpm$\n"
                )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "remove", SSL_CERT_REPO])
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), f"[{SSL_CERT_REPO}] repo removed ({path})")

    def test_remove_purge_cache_ignores_unknown_legacy_repo_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = os.path.join(tmp, "cache")
            cache_path = os.path.join(cache_root, SSL_CERT_REPO)
            os.makedirs(cache_path)
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    f"cache_dir = {cache_root}\n"
                    "\n"
                    f"[{SSL_CERT_REPO}]\n"
                    "url = https://github.com/jfut/sslcert-cli\n"
                    "asset_regex = .*\\.rpm$\n"
                )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "remove", SSL_CERT_REPO, "--purge-cache"])
            self.assertEqual(result, 0)
            self.assertFalse(os.path.exists(cache_path))

    def test_unset_prints_config_path_after_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            repo_path = self._repo_path(tmp, SSL_CERT_REPO)
            os.makedirs(os.path.dirname(repo_path))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"[main]\ninclude = {os.path.join(tmp, 'anyrepo.d')}\n")
            with open(repo_path, "w", encoding="utf-8") as fh:
                fh.write(
                    f"[{SSL_CERT_REPO}]\n"
                    "url = https://github.com/jfut/sslcert-cli\n"
                    "minimum_release_age = 1h\n"
                )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", SSL_CERT_REPO, "unset", "minimum_release_age"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        f"[{SSL_CERT_REPO}] minimum_release_age unset ({repo_path})",
                        "",
                        "NOTICE: Run refresh immediately to apply the configuration changes.",
                        f"-> dnf-anyrepo refresh {SSL_CERT_REPO} -f",
                    ]
                ),
            )

    def test_add_with_name_uses_alias_for_later_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            repo_path = self._repo_path(tmp, "sslcert")
            stdout = io.StringIO()
            # Store a user-selected alias so later commands can use that short name.
            with contextlib.redirect_stdout(stdout):
                result = main(
                    [
                        "--config",
                        path,
                        "add",
                        "https://github.com/jfut/sslcert-cli",
                        "--name",
                        "sslcert",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), f"[sslcert] repo added ({repo_path})")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "sslcert", "set", "minimum_release_age", "3d"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        f"[sslcert] minimum_release_age: global(3d) -> 3d ({repo_path})",
                        "",
                        "NOTICE: Run refresh immediately to apply the configuration changes.",
                        "-> dnf-anyrepo refresh sslcert -f",
                    ]
                ),
            )

    def test_add_with_short_name_uses_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            repo_path = self._repo_path(tmp, "sslcert")
            stdout = io.StringIO()
            # Verify the short alias flag stores the same repository name as --name.
            with contextlib.redirect_stdout(stdout):
                result = main(
                    [
                        "--config",
                        path,
                        "add",
                        "https://github.com/jfut/sslcert-cli",
                        "-n",
                        "sslcert",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), f"[sslcert] repo added ({repo_path})")

    def test_global_show_prints_main_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    "cache_dir = /tmp/anyrepo-cache\n"
                    "refresh_interval = 1h\n"
                    "minimum_release_age = 2d\n"
                    "debug = true\n"
                )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "global"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        "asset_exclude: (?:-debuginfo(?:-|[.])|-debugsource(?:-|[.])|[.]src[.]rpm$)",
                        "asset_include: .*\\.rpm$",
                        "cache_dir: /tmp/anyrepo-cache",
                        "debug: true",
                        "minimum_release_age: 2d",
                        "priority: 99",
                        "refresh_interval: 1h",
                    ]
                ),
            )

    def test_refresh_clears_dnf_metadata_for_repo(self):
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
        manager = mock.Mock()
        manager.refresh.return_value = False
        manager.repo.return_value = repo
        stdout = io.StringIO()

        with mock.patch("dnf_plugin_anyrepo.cli.RepositoryManager", return_value=manager):
            with mock.patch("dnf_plugin_anyrepo.cli.local_repo.clear_dnf_repo_metadata") as clear_mock:
                with contextlib.redirect_stdout(stdout):
                    result = main(["refresh", "prec"])

        self.assertEqual(result, 0)
        clear_mock.assert_called_once_with(
            [
                "github.com:jfut:prec",
                "github.com:jfut:prec-debuginfo",
                "github.com:jfut:prec-source",
            ]
        )
        self.assertEqual(stdout.getvalue().strip(), "[prec] unchanged (/etc/dnf/plugins/anyrepo.conf)")

    def test_global_unset_prints_config_path_after_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[main]\nminimum_release_age = 1h\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "global", "unset", "minimum_release_age"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        f"[main] minimum_release_age unset ({path})",
                        "",
                        "NOTICE: Run refresh immediately to apply the configuration changes.",
                        "-> dnf-anyrepo refresh -f",
                    ]
                ),
            )

    def test_refresh_missing_repo_returns_bracketed_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[main]\n")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(["--config", path, "refresh", "aaabbbccc"])
            self.assertEqual(result, 1)
            self.assertEqual(
                stderr.getvalue().strip(),
                f"[aaabbbccc] repo not found ({path})",
            )

    def test_refresh_prints_bracketed_status_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[nginx-module-fancyindex-rpm]\nurl = https://github.com/example/repo\n")
            stdout = io.StringIO()
            # Avoid provider work here and verify only the CLI output format.
            with mock.patch("dnf_plugin_anyrepo.cli.RepositoryManager.refresh", return_value=False):
                with contextlib.redirect_stdout(stdout):
                    result = main(["--config", path, "refresh", "nginx-module-fancyindex-rpm"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                f"[nginx-module-fancyindex-rpm] unchanged ({path})",
            )

    def test_global_set_prints_refresh_hint_for_refreshable_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[main]\nminimum_release_age = 0s\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "global", "set", "minimum_release_age", "0s"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        f"[main] minimum_release_age: 0s -> 0s ({path})",
                        "",
                        "NOTICE: Run refresh immediately to apply the configuration changes.",
                        "-> dnf-anyrepo refresh -f",
                    ]
                ),
            )

    def test_repo_set_prints_refresh_hint_for_refreshable_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            repo_path = self._repo_path(tmp, "firehol")
            os.makedirs(os.path.dirname(repo_path))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"[main]\ninclude = {os.path.join(tmp, 'anyrepo.d')}\n")
            with open(repo_path, "w", encoding="utf-8") as fh:
                fh.write("[firehol]\nurl = https://github.com/firehol/firehol\nminimum_release_age = 1h\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "firehol", "set", "minimum_release_age", "0s"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        f"[firehol] minimum_release_age: 1h -> 0s ({repo_path})",
                        "",
                        "NOTICE: Run refresh immediately to apply the configuration changes.",
                        "-> dnf-anyrepo refresh firehol -f",
                    ]
                ),
            )

    def test_list_prints_gpgcheck_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    "minimum_release_age = 3d\n"
                    "\n"
                    "[nmcli-cli]\n"
                    "url = https://github.com/jfut/nmcli-cli\n"
                    "gpgcheck = 0\n"
                    "\n"
                    "[prec]\n"
                    "url = https://github.com/jfut/prec\n"
                )
            stdout = io.StringIO()
            with mock.patch("dnf_plugin_anyrepo.cli.repo_switch_gpgcheck", return_value=True):
                with contextlib.redirect_stdout(stdout):
                    result = main(["--config", path, "list"])
            self.assertEqual(result, 0)
            lines = stdout.getvalue().splitlines()
            self.assertEqual(
                lines[0].split(),
                ["NAME", "SOURCE", "URL", "ENABLED", "GPGCHECK", "MIN_AGE"],
            )
            self.assertIn(
                "nmcli-cli github-release https://github.com/jfut/nmcli-cli yes 0 global(3d)",
                " ".join(lines[1].split()),
            )
            self.assertIn(
                "prec github-release https://github.com/jfut/prec yes global(1) global(3d)",
                " ".join(lines[2].split()),
            )

    def test_repo_show_prints_repository_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    "cache_dir = /tmp/anyrepo-cache\n"
                    "refresh_interval = 1h\n"
                    "minimum_release_age = 2d\n"
                    "\n"
                    "[prec]\n"
                    "url = https://github.com/jfut/prec\n"
                    "arch = x86_64\n"
                    "releasever = el9\n"
                )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "prec"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        "arch: x86_64",
                        "asset_exclude: global((?:-debuginfo(?:-|[.])|-debugsource(?:-|[.])|[.]src[.]rpm$))",
                        "asset_include: global(.*\\.rpm$)",
                        "cache_dir: global(/tmp/anyrepo-cache)",
                        "enabled: true",
                        "github_token_file:",
                        "gpgcheck: global(0)",
                        "minimum_release_age: global(2d)",
                        "priority: global(99)",
                        "refresh_interval: global(1h)",
                        "releasever: el9",
                        "source: github-release",
                        "url: https://github.com/jfut/prec",
                    ]
                ),
            )

    def test_set_prints_before_and_after_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            repo_path = self._repo_path(tmp, "nmcli-cli")
            os.makedirs(os.path.dirname(repo_path))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    f"include = {os.path.join(tmp, 'anyrepo.d')}\n"
                    "minimum_release_age = 3d\n"
                )
            with open(repo_path, "w", encoding="utf-8") as fh:
                fh.write("[nmcli-cli]\nurl = https://github.com/jfut/nmcli-cli\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "nmcli-cli", "set", "minimum_release_age", "1h"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        f"[nmcli-cli] minimum_release_age: global(3d) -> 1h ({repo_path})",
                        "",
                        "NOTICE: Run refresh immediately to apply the configuration changes.",
                        "-> dnf-anyrepo refresh nmcli-cli -f",
                    ]
                ),
            )

    def test_set_gpgcheck_prints_inherited_before_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            repo_path = self._repo_path(tmp, "prec")
            os.makedirs(os.path.dirname(repo_path))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"[main]\ninclude = {os.path.join(tmp, 'anyrepo.d')}\n")
            with open(repo_path, "w", encoding="utf-8") as fh:
                fh.write("[prec]\nurl = https://github.com/jfut/prec\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "prec", "set", "gpgcheck", "0"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                f"[prec] gpgcheck: global(0) -> 0 ({repo_path})",
            )

    def test_priority_can_be_set_globally_and_overridden_per_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\npriority = 50\n\n"
                    "[prec]\nurl = https://github.com/jfut/prec\n"
                )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "global", "set", "priority", "20"])
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), f"[main] priority: 50 -> 20 ({path})")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "prec", "set", "priority", "10"])
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), f"[prec] priority: global(20) -> 10 ({path})")

            config = load_config(path)
            self.assertEqual(config.repos["prec"].priority, 10)

    def test_global_set_prints_refresh_hint_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    "minimum_release_age = 3d\n"
                    "\n"
                    "[nmcli-cli]\n"
                    "url = https://github.com/jfut/nmcli-cli\n"
                    "\n"
                    "[prec]\n"
                    "url = https://github.com/jfut/prec\n"
                )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "global", "set", "minimum_release_age", "1h"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        f"[main] minimum_release_age: 3d -> 1h ({path})",
                        "",
                        "NOTICE: Run refresh immediately to apply the configuration changes.",
                        "-> dnf-anyrepo refresh -f",
                    ]
                ),
            )

    def test_global_set_debug_does_not_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[main]\ndebug = false\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "global", "set", "debug", "true"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                f"[main] debug: false -> true ({path})",
            )

    def test_repo_show_prints_repository_gpgcheck_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[prec]\nurl = https://github.com/jfut/prec\ngpgcheck = 1\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "prec"])
            self.assertEqual(result, 0)
            self.assertIn("gpgcheck: 1\n", stdout.getvalue())

    def test_set_missing_repo_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[main]\n")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(["--config", path, "repo", "missing", "set", "minimum_release_age", "1h"])
            self.assertEqual(result, 1)
            self.assertEqual(
                stderr.getvalue().strip(),
                f"[missing] repo not found ({path})",
            )

    def test_set_unknown_repo_key_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    "\n"
                    "[nmcli-cli]\n"
                    "url = https://github.com/jfut/nmcli-cli\n"
                )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(["--config", path, "repo", "nmcli-cli", "set", "unknown_key", "3d"])
            self.assertEqual(result, 1)
            self.assertEqual(
                stderr.getvalue().strip(),
                f"[nmcli-cli] unknown repo key unknown_key ({path})",
            )

    def test_repo_show_prints_repository_asset_exclude_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[prec]\n"
                    "url = https://github.com/jfut/prec\n"
                    "asset_exclude = -tests-.*\\.rpm$\n"
                )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "prec"])
            self.assertEqual(result, 0)
            self.assertIn("asset_exclude: -tests-.*\\.rpm$\n", stdout.getvalue())

    def test_set_asset_include_prints_inherited_before_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[prec]\nurl = https://github.com/jfut/prec\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "prec", "set", "asset_include", ".*module.*\\.rpm$"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        f"[prec] asset_include: global(.*\\.rpm$) -> .*module.*\\.rpm$ ({path})",
                        "",
                        "NOTICE: Run refresh immediately to apply the configuration changes.",
                        "-> dnf-anyrepo refresh prec -f",
                    ]
                ),
            )

    def test_set_asset_include_prints_refresh_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[prec]\nurl = https://github.com/jfut/prec\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "prec", "set", "asset_include", ".*module.*\\.rpm$"])
            self.assertEqual(result, 0)
            self.assertIn("-> dnf-anyrepo refresh prec -f\n", stdout.getvalue())

    def test_global_set_asset_exclude_prints_refresh_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    "asset_exclude = -tests-.*\\.rpm$\n"
                    "\n"
                    "[nmcli-cli]\n"
                    "url = https://github.com/jfut/nmcli-cli\n"
                )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "global", "set", "asset_exclude", "$^"])
            self.assertEqual(result, 0)
            self.assertIn("-> dnf-anyrepo refresh -f\n", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
