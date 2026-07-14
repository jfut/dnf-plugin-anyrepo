# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

"""Tests for the Bash completion script shipped in the RPM."""

import os
import subprocess
import tempfile
import unittest


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPLETION_PATH = os.path.join(ROOT_DIR, "packaging", "dnf-anyrepo.bash-completion")


class BashCompletionTest(unittest.TestCase):
    def _complete(self, *words, env=None):
        # Source the shipped script and invoke its registered completion function.
        quoted_words = " ".join("'{}'".format(word) for word in words)
        script = """
source '{completion_path}'
COMP_WORDS=({words})
COMP_CWORD=$(( ${{#COMP_WORDS[@]}} - 1 ))
_dnf_anyrepo_complete
printf '%s\\n' "${{COMPREPLY[@]}}"
""".format(completion_path=COMPLETION_PATH, words=quoted_words)
        result = subprocess.run(
            ["bash", "-c", script],
            check=True,
            stdout=subprocess.PIPE,
            universal_newlines=True,
            env=env,
        )
        return result.stdout.splitlines()

    def test_top_level_commands_are_completed(self):
        self.assertEqual(
            self._complete("dnf-anyrepo", "re"),
            ["remove", "refresh", "repo"],
        )

    def test_global_keys_are_completed(self):
        self.assertEqual(
            self._complete("dnf-anyrepo", "global", "set", "min"),
            ["minimum_release_age"],
        )

    def test_repository_boolean_values_are_completed(self):
        self.assertEqual(
            self._complete("dnf-anyrepo", "repo", "example", "set", "enabled", "f"),
            ["false"],
        )

    def test_add_enabled_values_are_completed(self):
        self.assertEqual(
            self._complete("dnf-anyrepo", "add", "--enabled", ""),
            ["0", "1"],
        )

    def test_repository_names_are_loaded_from_the_selected_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            command_path = os.path.join(tmp, "dnf-anyrepo")
            with open(command_path, "w", encoding="utf-8") as fh:
                # The completion script asks the CLI for names, as the CLI resolves includes.
                fh.write("#!/usr/bin/env bash\nprintf 'NAME SOURCE\\nexample github-release\\n'\n")
            os.chmod(command_path, 0o755)
            env = os.environ.copy()
            env["PATH"] = "{}:{}".format(tmp, env["PATH"])
            self.assertEqual(
                self._complete("dnf-anyrepo", "--config", "/tmp/custom.conf", "repo", "ex", env=env),
                ["example"],
            )
