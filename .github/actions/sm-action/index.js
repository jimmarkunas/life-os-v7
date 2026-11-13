// LIFE-636: cut from bitwarden/sm-action v3.0.1 index.js (GPL-3.0). Removed: the release download, the cargo build-from-source fallback, the version lookup, Windows.
// What is left: find the helper program that ../bws-helper seeded (and checked against helper.sha256), then run it with the job's inputs and output untouched, so its ::add-mask:: lines reach the runner.
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const ARCH = { x64: "x86_64", arm64: "aarch64" };
const PLATFORM = { linux: "unknown-linux-gnu", darwin: "apple-darwin" };

function binaryPath() {
  const arch = ARCH[process.arch];
  const platform = PLATFORM[process.platform];
  if (arch == null || platform == null) {
    throw new Error(`Unsupported platform: ${process.platform} ${process.arch}`);
  }
  return path.join(__dirname, "target", `${arch}-${platform}`, "release", "sm-action");
}

try {
  const binary = binaryPath();
  if (!fs.existsSync(binary)) {
    throw new Error("the checked helper program is missing (run ../bws-helper first); nothing is downloaded or built here");
  }
  fs.chmodSync(binary, 0o755);
  execFileSync(binary, { stdio: "inherit" });
} catch (error) {
  console.error(`Error: ${error.message}`);
  process.exit(1);
}
