# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

import contextlib
import io
import os
import tempfile
import unittest

from dnf_plugin_anyrepo.cli import main


SSL_CERT_REPO = "sslcert-cli"


class CliTest(unittest.TestCase):
    def test_add_prints_config_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            stdout = io.StringIO()
            # Confirm add output tells users where the repository was persisted.
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "add", "https://github.com/jfut/sslcert-cli"])
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), f"{path}: Added [{SSL_CERT_REPO}]")

    def test_add_existing_repo_prints_config_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "anyrepo.conf")
            main(["--config", path, "add", "https://github.com/jfut/sslcert-cli"])
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(["--config", path, "add", "https://github.com/jfut/sslcert-cli"])
            self.assertEqual(result, 1)
            self.assertEqual(
                stderr.getvalue().strip(),
                f"{path}: Repository already exists [{SSL_CERT_REPO}]",
            )

    def test_remove_prints_config_path(self):
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
            # Confirm remove output matches add and shows the modified config file.
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "remove", SSL_CERT_REPO])
            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().strip(), f"{path}: Removed [{SSL_CERT_REPO}]")

    def test_unset_prints_config_path(self):
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
                f"{path}: Unset [{SSL_CERT_REPO}] minimum_release_age",
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
            self.assertEqual(stdout.getvalue().strip(), f"{path}: Added [sslcert]")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(["--config", path, "repo", "sslcert", "set", "minimum_release_age", "3d"])
            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(),
                f"{path}: [sslcert] minimum_release_age: global(3d) -> 3d",
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
            self.assertEqual(stdout.getvalue().strip(), f"{path}: Added [sslcert]")

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

    def test_global_unset_prints_config_path(self):
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
                f"{path}: Unset [main] minimum_release_age",
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
                f"{path}: [nmcli-cli] minimum_release_age: global(3d) -> 1h",
            )

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
                f"{path}: Repository not found [missing]",
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
                f"{path}: Unknown repository key [nmcli-cli] unknown_key",
            )


if __name__ == "__main__":
    unittest.main()
