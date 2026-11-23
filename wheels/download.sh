#!/bin/bash
cd "$(dirname "$0")"
dl() {
  local url="$1" out="$2" expect="$3"
  for i in $(seq 1 200); do
    curl -sSL -C - --max-time 0 -o "$out" "$url" && break
    sleep 3
  done
  echo "$out: $(stat -f%z "$out" 2>/dev/null) bytes (expect $expect)"
}
dl "https://mirrors.aliyun.com/pypi/packages/37/81/aa9ab58ec10264c1abe62c8b73f5086c3c558885d6beecebf699f0dbeaeb/torch-2.6.0-cp310-cp310-manylinux1_x86_64.whl" "torch-2.6.0-cp310-cp310-manylinux1_x86_64.whl" "803682483"
dl "https://mirrors.aliyun.com/pypi/packages/ad/28/4dbe7e70966e16ebb90d5c887c12e3fc6d08a1c1ce0a79f8de357f0c36f9/torchaudio-2.6.0-cp310-cp310-manylinux1_x86_64.whl" "torchaudio-2.6.0-cp310-cp310-manylinux1_x86_64.whl" "3460347"
echo "DONE_ALL"
