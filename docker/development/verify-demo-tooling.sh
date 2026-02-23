#!/usr/bin/env bash
set -euo pipefail

required_commands=(
  bash
  uv
  docker
  ffmpeg
  jq
  node
  npm
  npx
  xauth
  xdotool
  xvfb-run
  Xvfb
)

for cmd in "${required_commands[@]}"; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    printf "Missing required command for demo UI capture: %s\n" "${cmd}" >&2
    exit 1
  fi
done

uv --version >/dev/null
node --version >/dev/null
npm --version >/dev/null
docker --version >/dev/null
ffmpeg -version >/dev/null
jq --version >/dev/null

if [ ! -d "/ms-playwright" ]; then
  printf "Playwright browsers directory not found: /ms-playwright\n" >&2
  exit 1
fi

node --input-type=module <<'NODE'
import { firefox } from "playwright";

const executable = firefox.executablePath();
if (!executable) {
  throw new Error("Playwright Firefox executable path was empty.");
}
const browser = await firefox.launch({ headless: true });
const page = await browser.newPage();
await page.setContent("<html><body><h1>demo-ready</h1></body></html>");
await browser.close();
process.stdout.write(`[verify-demo-tooling] headless Firefox OK: ${executable}\n`);
NODE

xvfb-run -a --server-args="-screen 0 1280x720x24 -ac +extension RANDR" \
  bash -euo pipefail -c '
    xdotool mousemove 64 64
    xdotool getmouselocation --shell > /tmp/verify-demo-xdotool.log
    ffmpeg -hide_banner -loglevel error -y \
      -f x11grab \
      -framerate 5 \
      -video_size 1280x720 \
      -t 1 \
      -i "${DISPLAY}.0+0,0" \
      -f null -
  '

xvfb-run -a --server-args="-screen 0 1280x720x24 -ac +extension RANDR" \
  node --input-type=module <<'NODE'
import { firefox } from "playwright";

const browser = await firefox.launch({
  headless: false,
  args: ["--width=1024", "--height=768"]
});
const page = await browser.newPage({
  viewport: {
    width: 1024,
    height: 768
  }
});
await page.goto("data:text/html,<title>demo-ready</title><main>demo-ready</main>", {
  waitUntil: "domcontentloaded"
});
await browser.close();
process.stdout.write("[verify-demo-tooling] headful Firefox under Xvfb OK\n");
NODE
