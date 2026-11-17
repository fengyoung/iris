#!/bin/bash
set -euo pipefail
OCR_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
mkdir -p "${OCR_ROOT}/.build"
xcrun swiftc -O -framework AppKit -framework Vision \
  -module-cache-path "${OCR_ROOT}/.build/module-cache" \
  "${OCR_ROOT}/Sources/Helper.swift" -o "${OCR_ROOT}/.build/iris-ocr-helper"
"${OCR_ROOT}/.build/iris-ocr-helper" --self-test
