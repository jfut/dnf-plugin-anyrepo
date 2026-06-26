# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from dnf_plugin_anyrepo.cli import main


SSL_CERT_REPO = "sslcert-cli"


class CliTest(unittest.TestCase):
    def test_add_prints_config_path_after_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            stdout = io.StringIO()
            # Show the added repository first, then the config file that changed.
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "add", "https://github.com/jfut/sslcert-cli"])
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), f"[{SSL_CERT_REPO}] repo added ({path})")

    def test_add_existing_repo_prints_config_path_after_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with contextlib.redirect_stdout(io.StringIO()):
                main(["--config", path, "add", "https://github.com/jfut/sslcert-cli"])
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(["--config", path, "add", "https://github.com/jfut/sslcert-cli"])
            self.assertEqual(result, 1)
            self.assertEqual(
                stderr.getvalue().strip(),
                f"[{SSL_CERT_REPO}] repo already exists ({path})",
            )

    def test_remove_prints_config_path_after_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    "\n"
                    f"[{SSL_CERT_REPO}]\n"
                    "url = https://github.com/jfut/sslcert-cli\n"
                )
            stdout = io.StringIO()
            # Show the removed repository first, then the config file that changed.
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "remove", SSL_CERT_REPO])
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), f"[{SSL_CERT_REPO}] repo removed ({path})")

    def test_unset_prints_config_path_after_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    "\n"
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
                        f"[{SSL_CERT_REPO}] minimum_release_age unset ({path})",
                        "",
                        "NOTICE: Run refresh immediately to apply the configuration changes.",
                        f"-> dnf-anyrepo refresh {SSL_CERT_REPO} -f",
                    ]
                ),
            )

    def test_add_with_name_uses_alias_for_later_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
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
            self.assertEqual(stdout.getvalue().strip(), f"[sslcert] repo added ({path})")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "sslcert", "set", "minimum_release_age", "3d"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        f"[sslcert] minimum_release_age: global(3d) -> 3d ({path})",
                        "",
                        "NOTICE: Run refresh immediately to apply the configuration changes.",
                        "-> dnf-anyrepo refresh sslcert -f",
                    ]
                ),
            )

    def test_add_with_short_name_uses_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
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
            self.assertEqual(stdout.getvalue().strip(), f"[sslcert] repo added ({path})")

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
                        "cache_dir: /tmp/anyrepo-cache",
                        "debug: true",
                        "minimum_release_age: 2d",
                        "refresh_interval: 1h",
                    ]
                ),
            )

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
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[firehol]\nurl = https://github.com/firehol/firehol\nminimum_release_age = 1h\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "firehol", "set", "minimum_release_age", "0s"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        f"[firehol] minimum_release_age: 1h -> 0s ({path})",
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
                        "asset_regex: .*\\.rpm$",
                        "cache_dir: global(/tmp/anyrepo-cache)",
                        "enabled: true",
                        "github_token_file:",
                        "gpgcheck: global(0)",
                        "minimum_release_age: global(2d)",
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
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "[main]\n"
                    "minimum_release_age = 3d\n"
                    "\n"
                    "[nmcli-cli]\n"
                    "url = https://github.com/jfut/nmcli-cli\n"
                )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "nmcli-cli", "set", "minimum_release_age", "1h"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                "\n".join(
                    [
                        f"[nmcli-cli] minimum_release_age: global(3d) -> 1h ({path})",
                        "",
                        "NOTICE: Run refresh immediately to apply the configuration changes.",
                        "-> dnf-anyrepo refresh nmcli-cli -f",
                    ]
                ),
            )

    def test_set_gpgcheck_prints_inherited_before_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[prec]\nurl = https://github.com/jfut/prec\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "prec", "set", "gpgcheck", "0"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                f"[prec] gpgcheck: global(0) -> 0 ({path})",
            )

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


if __name__ == "__main__":
    unittest.main()
