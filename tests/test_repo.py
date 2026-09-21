# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

import unittest
from unittest import mock

from dnf_plugin_anyrepo import repo


class RepoTest(unittest.TestCase):
    def test_run_createrepo_suppresses_createrepo_output(self):
        with mock.patch.object(repo.shutil, "which", return_value="/usr/bin/createrepo_c"):
            with mock.patch.object(repo.subprocess, "run") as run_mock:
                repo.run_createrepo("/tmp/cache")

        run_mock.assert_called_once_with(
            ["/usr/bin/createrepo_c", "--quiet", "--update", "/tmp/cache"],
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
