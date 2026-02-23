from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_hub.server as hub_server


class HubUiWidgetScriptTests(unittest.TestCase):
    def _extract_script(self) -> str:
        html = hub_server._html_page()
        match = re.search(r"<script>(.*?)</script>", html, flags=re.DOTALL)
        self.assertIsNotNone(match, "Hub HTML script block not found")
        return match.group(1)

    def test_add_widget_functions_append_rows(self) -> None:
        script = self._extract_script()
        node_script = f"""
const vm = require('vm');

class Element {{
  constructor(id = '') {{
    this.id = id;
    this.className = '';
    this.children = [];
    this.style = {{}};
    this.textContent = '';
    this.value = '';
    this.placeholder = '';
    this._innerHTML = '';
  }}

  set innerHTML(v) {{
    this._innerHTML = v;
    if (v === '') this.children = [];
  }}

  get innerHTML() {{
    return this._innerHTML;
  }}

  appendChild(el) {{
    this.children.push(el);
    el.parentNode = this;
    return el;
  }}

  querySelectorAll(selector) {{
    if (selector === '.widget-row.volume') {{
      return this.children.filter((c) => c.className === 'widget-row volume');
    }}
    if (selector === '.widget-row.env') {{
      return this.children.filter((c) => c.className === 'widget-row env');
    }}
    return [];
  }}

  closest() {{
    return this;
  }}
}}

const elements = {{
  'project-base-image-mode': Object.assign(new Element('project-base-image-mode'), {{ value: 'tag' }}),
  'project-base-image-value': new Element('project-base-image-value'),
  'project-default-volumes': new Element('project-default-volumes'),
  'project-default-env': new Element('project-default-env'),
  'projects': new Element('projects'),
  'chats': new Element('chats'),
  'ui-error': new Element('ui-error'),
}};

global.document = {{
  getElementById: (id) => elements[id] || null,
  createElement: () => new Element(),
  addEventListener: () => {{}},
  activeElement: null,
}};

global.alert = () => {{}};
global.fetch = async () => ({{
  ok: true,
  status: 200,
  json: async () => ({{ projects: [], chats: [] }}),
  text: async () => '',
}});
global.setInterval = () => 1;
global.confirm = () => true;

vm.runInThisContext({json.dumps(script)});
addVolumeRow('project-default-volumes');
addEnvRow('project-default-env');

if (elements['project-default-volumes'].children.length !== 1) {{
  throw new Error('Add volume did not append a row');
}}
if (elements['project-default-env'].children.length !== 1) {{
  throw new Error('Add environment variable did not append a row');
}}
"""
        result = subprocess.run(
            ["node", "-e", node_script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Node UI script test failed:\\nSTDOUT:\\n{result.stdout}\\nSTDERR:\\n{result.stderr}",
        )

    def test_base_image_placeholder_defaults_to_ubuntu_24_04(self) -> None:
        script = self._extract_script()
        node_script = f"""
const vm = require('vm');

class Element {{
  constructor(id = '') {{
    this.id = id;
    this.className = '';
    this.children = [];
    this.style = {{}};
    this.textContent = '';
    this.value = '';
    this.placeholder = '';
    this._innerHTML = '';
  }}

  set innerHTML(v) {{
    this._innerHTML = v;
    if (v === '') this.children = [];
  }}

  get innerHTML() {{
    return this._innerHTML;
  }}

  appendChild(el) {{
    this.children.push(el);
    el.parentNode = this;
    return el;
  }}
}}

const elements = {{
  'project-base-image-mode': Object.assign(new Element('project-base-image-mode'), {{ value: 'tag' }}),
  'project-base-image-value': new Element('project-base-image-value'),
  'project-default-volumes': new Element('project-default-volumes'),
  'project-default-env': new Element('project-default-env'),
  'projects': new Element('projects'),
  'chats': new Element('chats'),
  'ui-error': new Element('ui-error'),
}};

global.document = {{
  getElementById: (id) => elements[id] || null,
  createElement: () => new Element(),
  addEventListener: () => {{}},
  activeElement: null,
}};

global.alert = () => {{}};
global.fetch = async () => ({{
  ok: true,
  status: 200,
  json: async () => ({{ projects: [], chats: [] }}),
  text: async () => '',
}});
global.setInterval = () => 1;
global.confirm = () => true;

vm.runInThisContext({json.dumps(script)});

if (baseInputPlaceholder('tag') !== 'ubuntu:24.04') {{
  throw new Error(`Unexpected tag placeholder: ${{baseInputPlaceholder('tag')}}`);
}}

updateBasePlaceholderForCreate();
if (elements['project-base-image-value'].placeholder !== 'ubuntu:24.04') {{
  throw new Error(`Unexpected input placeholder: ${{elements['project-base-image-value'].placeholder}}`);
}}
"""
        result = subprocess.run(
            ["node", "-e", node_script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Node UI placeholder test failed:\\nSTDOUT:\\n{result.stdout}\\nSTDERR:\\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
