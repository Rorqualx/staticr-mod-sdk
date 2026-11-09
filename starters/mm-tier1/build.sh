#!/bin/sh
# Build my-mm-mod.wasm without the SDK tool: clang with the wasm32 target, then wasm-ld (or rust-lld
# from a stable Rust toolchain: rust-lld -flavor wasm). `staticr-mods build .` does the same and
# also checks the module against the host's imports and exports.
set -eu
cd "$(dirname "$0")"
LD="${WASM_LD:-wasm-ld}"
mkdir -p build
for c in *.c; do
  clang --target=wasm32 -O2 -nostdlib -fno-builtin -Wall -Wextra -I . -c "$c" -o "build/${c%.c}.o"
done
$LD --no-entry -o my-mm-mod.wasm build/*.o
echo "built my-mm-mod.wasm"
