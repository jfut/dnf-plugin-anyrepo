# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

import os
import tempfile
import unittest

from dnf_plugin_anyrepo.config import ConfigError, MainConfig, PluginConfig, RepoConfig
from dnf_plugin_anyrepo.manager import RepositoryManager
from dnf_plugin_anyrepo.state import save_state


class RepositoryManagerTest(unittest.TestCase):
    def test_refresh_missing_repo_raises_config_error(self):
        config = PluginConfig(path="", main=MainConfig(), repos={})
        manager = RepositoryManager(config=config)

        with self.assertRaisesRegex(ConfigError, "repository not found: missing"):
            manager.refresh("missing")

    def test_needs_refresh_when_cached_arch_differs(self):
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
                arch="x86_64",
            )
            os.makedirs(os.path.join(repo.cache_path, "repodata"))
            save_state(
                repo.cache_path,
                {
                    "arch": "aarch64",
                    "last_refresh_at": "2026-06-21T00:00:00Z",
                },
            )

            config = PluginConfig(path="", main=MainConfig(cache_dir=tmp), repos={"prec": repo})
            manager = RepositoryManager(config=config)
            self.assertTrue(manager.needs_refresh(repo))

    def test_needs_refresh_when_cached_releasever_differs(self):
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
                arch="x86_64",
                releasever="el9",
            )
            os.makedirs(os.path.join(repo.cache_path, "repodata"))
            save_state(
                repo.cache_path,
                {
                    "arch": "x86_64",
                    "releasever": "el10",
                    "last_refresh_at": "2026-06-21T00:00:00Z",
                },
            )

            config = PluginConfig(path="", main=MainConfig(cache_dir=tmp), repos={"prec": repo})
            manager = RepositoryManager(config=config)
            self.assertTrue(manager.needs_refresh(repo))


if __name__ == "__main__":
    unittest.main()
