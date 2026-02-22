# Mobile Wrapper (Capacitor): Android + iOS

This repo now includes a Capacitor wrapper around the existing Agent Hub web UI.

The wrapper supports two run modes:

- Hosted mode: load your running hub server (recommended for local development).
- Bundled mode: package static `web/dist` into the app.

For your use case ("wrap the existing web server"), use hosted mode with `--server-url`.

## Why this setup

- Android builds can run directly from Linux.
- iOS builds cannot run natively from Linux, so the workflow uses SSH to a macOS machine with Xcode.
- Scripts are idempotent:
  - Add native platforms only when missing.
  - Sync Capacitor config each run.
  - Target one explicit device for deterministic deploy behavior.

## Files added

- `web/capacitor.config.json`: default Capacitor config.
- `tools/mobile/configure_capacitor.sh`: generates `web/capacitor.config.json` (hosted or bundled mode).
- `tools/mobile/deploy_android.sh`: Linux Android build + install.
- `tools/mobile/deploy_ios_on_macos.sh`: iOS build + install on macOS.
- `tools/mobile/deploy_ios_via_ssh.sh`: Linux orchestration script for remote macOS iOS deploy.
- `tools/mobile/deploy_phone.sh`: single wrapper command that dispatches by target platform.

## Prerequisites

### Linux machine (your primary dev box)

- Node.js 20+ and Corepack.
- Android Studio SDK + platform tools (`adb`) + JDK 17+.
- USB debugging enabled on Android phone.
- SSH access to macOS machine for iOS builds.
- `rsync` and `ssh`.

### macOS machine (remote iOS builder)

- Xcode + Xcode Command Line Tools.
- CocoaPods (`pod` command).
- Apple Developer account/provisioning configured in Xcode.
- iPhone connected to that macOS machine (USB), trusted, Developer Mode enabled.

### Network requirements (hosted mode)

- Phone must reach the hub URL you pass in `--server-url`.
- Use a LAN-reachable address, not `127.0.0.1`.
  - Example: `http://192.168.1.20:8765`
- If your hub is running in Docker, ensure port publishing and host routing are reachable from phone and macOS.

### Container-in-container constraints

- If you run these scripts from inside a container/devbox:
  - `adb` USB passthrough must be exposed into that container.
  - Android SDK/JDK paths must be available in that container environment.
  - `--server-url` must be reachable from the phone itself, not just from inside the container namespace.

## 1) Android deploy from Linux

Start your hub server on Linux first (example):

```bash
uv run agent_hub --host 0.0.0.0 --port 8765
```

Then deploy with the wrapper:

```bash
tools/mobile/deploy_phone.sh android \
  --server-url http://<linux-lan-ip>:8765
```

Optional device targeting when multiple phones/emulators are connected:

```bash
tools/mobile/deploy_phone.sh android \
  --server-url http://<linux-lan-ip>:8765 \
  --device-id <adb-serial>
```

What the script does:

1. Validates required tools (`node`, `corepack`, `adb`, `java`).
2. Verifies one target Android device.
3. Writes `web/capacitor.config.json` with your server URL.
4. Runs `yarn install` and `yarn build`.
5. Adds Android platform if missing.
6. Runs `cap sync android`.
7. Runs Gradle `installDebug`.
8. Launches app on device.

## 2) iOS deploy from Linux via remote macOS (SSH)

First, identify your iPhone UDID on macOS:

```bash
xcrun xctrace list devices
```

Then run from Linux with the wrapper:

```bash
tools/mobile/deploy_phone.sh ios \
  --mac-host <user>@<mac-host> \
  --remote-dir ~/agent_hub_mobile \
  --server-url http://<linux-lan-ip>:8765 \
  --device-udid <iphone-udid> \
  --apple-team-id <team-id>
```

What this does:

1. Rsyncs repo content to macOS (excluding caches/build outputs).
2. Runs `tools/mobile/deploy_ios_on_macos.sh` remotely.
3. Remote script:
  - Validates `xcodebuild`, `xcrun`, `pod`, `node`, `corepack`.
  - Generates Capacitor config.
  - Builds web assets.
  - Adds/syncs iOS platform.
  - Enables iOS ATS cleartext allowance when `--server-url` is `http://...`.
  - Builds/signs with Xcode for your device.
  - Installs and launches via `xcrun devicectl` (or `ios-deploy` fallback).

## Manual iOS deploy directly on macOS (optional)

If you are already logged into macOS shell:

```bash
tools/mobile/deploy_phone.sh ios \
  --server-url http://<linux-lan-ip>:8765 \
  --device-udid <iphone-udid> \
  --apple-team-id <team-id>
```

Force local mode explicitly (macOS only):

```bash
tools/mobile/deploy_phone.sh ios-local \
  --server-url http://<linux-lan-ip>:8765 \
  --device-udid <iphone-udid> \
  --apple-team-id <team-id>
```

## Config-only usage

To regenerate only the Capacitor config:

Hosted mode:

```bash
tools/mobile/configure_capacitor.sh \
  --app-id com.agenthub.mobile \
  --app-name "Agent Hub" \
  --server-url http://<linux-lan-ip>:8765
```

Bundled mode (no server URL):

```bash
tools/mobile/configure_capacitor.sh \
  --app-id com.agenthub.mobile \
  --app-name "Agent Hub"
```

## Important assumptions and failure modes

- `--server-url` must be reachable from the phone.
  - Failure symptom: app opens but cannot load UI.
- HTTP hosted mode is enabled for local development convenience.
  - iOS script sets `NSAllowsArbitraryLoads=true` when URL is `http://`.
  - For production, prefer HTTPS and tighten ATS policy.
- iOS signing/provisioning must be valid for the bundle ID.
  - Failure symptom: Xcode signing errors during build/install.
- Android SDK/Gradle/JDK mismatch can fail `installDebug`.
  - Failure symptom: Gradle task errors in `web/android`.

## Validation checklist

After deploy:

1. App launches on device.
2. App home screen loads Agent Hub UI.
3. API calls succeed (project/chat list loads).
4. Terminal streams function over websocket.
5. Artifact listing/download still works.
