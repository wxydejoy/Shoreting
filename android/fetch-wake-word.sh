#!/bin/sh
# 下载「小岸」唤醒词与中文流式 transducer（热词需要 beam search，CTC 用不了）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
AAR_DIR="$ROOT/app/libs"
KWS_DIR="$ROOT/app/src/main/assets/kws"
ASR_DIR="$ROOT/app/src/main/assets/asr"
mkdir -p "$AAR_DIR" "$KWS_DIR" "$ASR_DIR"

AAR="$AAR_DIR/sherpa-onnx-1.13.6.aar"
if [ ! -s "$AAR" ]; then
  curl -L --fail -o "$AAR" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.6/sherpa-onnx-1.13.6.aar"
fi

if [ ! -s "$KWS_DIR/encoder.int8.onnx" ] || [ ! -s "$KWS_DIR/decoder.onnx" ] || [ ! -s "$KWS_DIR/joiner.int8.onnx" ]; then
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -L --fail -o "$TMP/kws-mobile.tar.bz2" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile.tar.bz2"
  tar xjf "$TMP/kws-mobile.tar.bz2" -C "$TMP"
  SRC="$TMP/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile"
  cp "$SRC/encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx" "$KWS_DIR/encoder.int8.onnx"
  cp "$SRC/decoder-epoch-12-avg-2-chunk-16-left-64.onnx" "$KWS_DIR/decoder.onnx"
  cp "$SRC/joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx" "$KWS_DIR/joiner.int8.onnx"
  cp "$SRC/tokens.txt" "$KWS_DIR/tokens.txt"
  rm -rf "$TMP"
  trap - EXIT
fi

# 旧版 small-CTC 单文件，热词无效，换 transducer 后删掉以免打进 APK。
rm -f "$ASR_DIR/model.int8.onnx"

if [ ! -s "$ASR_DIR/encoder.int8.onnx" ] || [ ! -s "$ASR_DIR/decoder.onnx" ] || [ ! -s "$ASR_DIR/joiner.int8.onnx" ]; then
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  TAR="$TMP/asr-zh.tar.bz2"
  if [ -s /tmp/asr-zh/model.tar.bz2 ]; then
    cp /tmp/asr-zh/model.tar.bz2 "$TAR"
  fi
  if [ ! -s "$TAR" ]; then
    curl -L --fail -o "$TAR" \
      "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30.tar.bz2"
  fi
  tar xjf "$TAR" -C "$TMP"
  SRC="$TMP/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30"
  cp "$SRC/encoder.int8.onnx" "$ASR_DIR/encoder.int8.onnx"
  cp "$SRC/decoder.onnx" "$ASR_DIR/decoder.onnx"
  cp "$SRC/joiner.int8.onnx" "$ASR_DIR/joiner.int8.onnx"
  cp "$SRC/tokens.txt" "$ASR_DIR/tokens.txt"
fi

echo "voice models ready"
