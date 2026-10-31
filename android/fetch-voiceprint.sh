#!/bin/sh
# 下载声纹模型（F5）：CAM++ 中文说话人向量，本地认出「谁在说话」用。
# 只在本机算向量，模型装不装都不影响她说话。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
VP_DIR="$ROOT/app/src/main/assets/voiceprint"
mkdir -p "$VP_DIR"

if [ ! -s "$VP_DIR/model.onnx" ]; then
  curl -L --fail -o "$VP_DIR/model.onnx" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
fi

echo "voiceprint model ready"
