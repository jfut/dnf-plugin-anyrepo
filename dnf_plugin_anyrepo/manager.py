# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

"""High-level operations shared by the DNF plugin and management CLI."""

import os
import time
from typing import Iterable, Optional

from dnf_plugin_anyrepo.config import ConfigError, PluginConfig, RepoConfig, load_config
from dnf_plugin_anyrepo.providers.github_release import GitHubReleaseProvider
from dnf_plugin_anyrepo.state import load_state


def provider_for(config: RepoConfig):
    if config.source == "github-release":
        return GitHubReleaseProvider(config)
    raise ValueError(f"unsupported source: {config.source}")


class RepositoryManager:
    def __init__(self, config: Optional[PluginConfig] = None, config_path: Optional[str] = None):
        self.config = config or (load_config(config_path) if config_path else load_config())

    def enabled_repos(self) -> Iterable[RepoConfig]:
        return (repo for repo in self.config.repos.values() if repo.enabled)

    def refresh(self, name: str, force: bool = False) -> bool:
        repo = self.repo(name)
        if not repo.enabled and not force:
            return False
        if not force and not self.needs_refresh(repo):
            return False
        return provider_for(repo).refresh()

    def refresh_all(self, force=False):
        results = []
        for repo in sorted(self.config.repos.values(), key=lambda item: item.name):
            if repo.enabled or force:
                results.append((repo.name, self.refresh(repo.name, force=force)))
        return results

    def repo(self, name: str) -> RepoConfig:
        """Return a configured repository or raise a user-facing config error."""

        try:
            return self.config.repos[name]
        except KeyError as exc:
            raise ConfigError(f"repository not found: {name}") from exc

    def needs_refresh(self, repo: RepoConfig) -> bool:
        state = load_state(repo.cache_path)
        if not state:
            return True
        if state.get("arch") != repo.arch:
            return True
        if state.get("releasever") != repo.releasever:
            return True
        if not os.path.isdir(os.path.join(repo.cache_path, "repodata")):
            return True
        refreshed = state.get("last_refresh_at")
        if not refreshed:
            return True
        try:
            from datetime import datetime, timezone

            value = str(refreshed)
            if value.endswith("Z"):
                value = value[:-1]
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            return time.time() - parsed.timestamp() >= repo.refresh_interval
        except ValueError:
            return True
