# Source this file in bash/zsh. No shell profile or global installation is changed.
export ALIGN_TOOLCHAIN_DIR="${ALIGN_TOOLCHAIN_DIR:-$HOME/Library/Application Support/ALIGN/toolchain}"
export ALIGN_CACHE_DIR="${ALIGN_CACHE_DIR:-$HOME/Library/Caches/ALIGN}"
case "$(uname -m)" in
  arm64) align_node_arch=arm64; align_uv_arch=aarch64 ;;
  x86_64) align_node_arch=x64; align_uv_arch=x86_64 ;;
esac
export PATH="$ALIGN_TOOLCHAIN_DIR/bin:$ALIGN_TOOLCHAIN_DIR/node-v22.22.0-darwin-$align_node_arch/bin:$ALIGN_TOOLCHAIN_DIR/uv-$align_uv_arch-apple-darwin:$PATH"
export COREPACK_HOME="$ALIGN_CACHE_DIR/corepack"
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
export UV_CACHE_DIR="$ALIGN_CACHE_DIR/uv"
export UV_PYTHON_INSTALL_DIR="$ALIGN_TOOLCHAIN_DIR/python"
export UV_PYTHON=3.12.11
export PLAYWRIGHT_BROWSERS_PATH="$ALIGN_CACHE_DIR/playwright"
export npm_config_cache="$ALIGN_CACHE_DIR/npm"
