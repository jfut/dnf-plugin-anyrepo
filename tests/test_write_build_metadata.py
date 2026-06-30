# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

import tempfile
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parent.parent / "packaging" / "write_build_metadata.py"
MODULE_SPEC = spec_from_file_location("write_build_metadata", MODULE_PATH)
write_build_metadata = module_from_spec(MODULE_SPEC)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
MODULE_SPEC.loader.exec_module(write_build_metadata)


class WriteBuildMetadataTest(unittest.TestCase):
    def test_snapshot_falls_back_without_git_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp) / "goreleaser" / "dnf_plugin_anyrepo"
            with mock.patch.object(write_build_metadata, "BUILD_DIR", build_dir), mock.patch.object(
                write_build_metadata,
                "_git_output_or_none",
                return_value=None,
            ), mock.patch.dict("os.environ", {}, clear=True), mock.patch(
                "sys.argv",
                ["write_build_metadata.py", "snapshot"],
            ):
                result = write_build_metadata.main()

            self.assertEqual(result, 0)
            init_source = (build_dir / "__init__.py").read_text(encoding="utf-8")
            self.assertIn('__version__ = "0.dev0"', init_source)
            self.assertIn('__commit__ = "none"', init_source)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
