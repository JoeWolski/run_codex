from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WebCreateProjectConfigModeTests(unittest.TestCase):
    def test_config_mode_normalization(self) -> None:
        node_script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import {
              isAutoCreateProjectConfigMode,
              normalizeCreateProjectConfigMode,
              shouldShowManualProjectConfigInputs
            } from "./web/src/createProjectConfigMode.js";

            assert.equal(normalizeCreateProjectConfigMode("manual"), "manual");
            assert.equal(normalizeCreateProjectConfigMode("MANUAL"), "manual");
            assert.equal(normalizeCreateProjectConfigMode("auto"), "auto");
            assert.equal(normalizeCreateProjectConfigMode(""), "auto");
            assert.equal(normalizeCreateProjectConfigMode(undefined), "auto");
            assert.equal(normalizeCreateProjectConfigMode("unexpected"), "auto");

            assert.equal(isAutoCreateProjectConfigMode("auto"), true);
            assert.equal(isAutoCreateProjectConfigMode("manual"), false);
            assert.equal(isAutoCreateProjectConfigMode("anything"), true);

            assert.equal(shouldShowManualProjectConfigInputs("manual"), true);
            assert.equal(shouldShowManualProjectConfigInputs("auto"), false);
            assert.equal(shouldShowManualProjectConfigInputs("anything"), false);
            """
        )

        result = subprocess.run(
            ["node", "--input-type=module", "-e", node_script],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Node create-project config mode test failed:\\nSTDOUT:\\n{result.stdout}\\nSTDERR:\\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
