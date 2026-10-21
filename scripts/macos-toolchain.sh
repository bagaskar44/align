#!/bin/bash
# Explicitly invoked installer; all generated files stay in the selected prefix/cache.
set -euo pipefail
test "$(uname -s)" = Darwin || { echo 'This installer requires macOS.' >&2; exit 1; }
script_dir=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=macos-env.sh
source "$script_dir/macos-env.sh"
case "$(uname -m)" in
  arm64) uv_arch=aarch64 ;;
  x86_64) uv_arch=x86_64 ;;
  *) echo 'Unsupported Mac architecture.' >&2; exit 1 ;;
esac
# uv names Apple Silicon aarch64, whereas Node names it arm64.
uv_dir="$ALIGN_TOOLCHAIN_DIR/uv-$uv_arch-apple-darwin"
export PATH="$uv_dir:$PATH"
mkdir -p "$ALIGN_TOOLCHAIN_DIR/bin" "$ALIGN_CACHE_DIR/downloads"
download_dir=$(mktemp -d "$ALIGN_CACHE_DIR/downloads/install.XXXXXX")
echo "Toolchain prefix: $ALIGN_TOOLCHAIN_DIR"
echo "Download receipts: $download_dir"
node_archive="node-v22.22.0-darwin-$align_node_arch.tar.gz"
if [ ! -x "$ALIGN_TOOLCHAIN_DIR/${node_archive%.tar.gz}/bin/node" ]; then
  curl --fail --location --retry 3 --silent --show-error "https://nodejs.org/dist/v22.22.0/$node_archive" -o "$download_dir/$node_archive"
  curl --fail --location --retry 3 --silent --show-error https://nodejs.org/dist/v22.22.0/SHASUMS256.txt -o "$download_dir/node-SHASUMS256.txt"
  (cd "$download_dir"; awk -v name="$node_archive" '$2 == name { print; found=1 } END { if (!found) exit 1 }' node-SHASUMS256.txt | shasum -a 256 -c -)
  tar -xzf "$download_dir/$node_archive" -C "$ALIGN_TOOLCHAIN_DIR"
fi
uv_archive="uv-$uv_arch-apple-darwin.tar.gz"
if [ ! -x "$uv_dir/uv" ]; then
  uv_url="https://github.com/astral-sh/uv/releases/download/0.8.22/$uv_archive"
  curl --fail --location --retry 3 --silent --show-error "$uv_url" -o "$download_dir/$uv_archive"
  curl --fail --location --retry 3 --silent --show-error "$uv_url.sha256" -o "$download_dir/uv.sha256"
  (cd "$download_dir"; expected=$(awk '{print $1}' uv.sha256); printf '%s  %s\n' "$expected" "$uv_archive" | shasum -a 256 -c -)
  tar -xzf "$download_dir/$uv_archive" -C "$ALIGN_TOOLCHAIN_DIR"
fi
# Stable commands for the sourced environment, without editing ~/.local/bin.
ln -sf "$uv_dir/uv" "$ALIGN_TOOLCHAIN_DIR/bin/uv"
ln -sf "$uv_dir/uvx" "$ALIGN_TOOLCHAIN_DIR/bin/uvx"
uv python install --no-bin 3.12.11
python_path=$(uv python find --no-project --managed-python 3.12.11)
ln -sf "$python_path" "$ALIGN_TOOLCHAIN_DIR/bin/python3"
corepack enable
# Running from the repository selects packageManager=pnpm@10.18.3.
cd "$script_dir/.."
node --version
uv --version
python3 --version
corepack pnpm --version
echo 'Ready. Source scripts/macos-env.sh in each terminal using this toolchain.'
