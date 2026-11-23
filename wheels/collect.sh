#!/bin/bash
# 收集镜像构建所需的全部 wheel 到 wheels/：
# 1. 宿主机 pip download 解析（--only-binary 平台约束）
# 2. 遇到 sdist-only 的包：纯 Python 包宿主机直接 pip wheel；
#    含 C 扩展的包在本地 python:3.10-slim 容器里打 manylinux wheel
# 3. 循环直到解析成功
set -u
cd "$(dirname "$0")"
PYIMAGE="docker.m.daocloud.io/library/python:3.10-slim-bullseye"
VENV_PY="../.venv/bin/python"

main_pass() {
  "$VENV_PY" -m pip download -d . --find-links . \
    --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 \
    --platform manylinux_2_17_x86_64 --platform manylinux1_x86_64 --platform linux_x86_64 \
    --python-version 310 --implementation cp --only-binary=:all: \
    -i https://mirrors.aliyun.com/pypi/simple \
    torch==2.6.0 torchaudio==2.6.0 -r ../requirements.txt 2>&1
}

has_wheel() { ls . | grep -qi "^${1//-/_}-.*\.whl$"; }

for round in $(seq 1 12); do
  out=$(main_pass) || true
  if echo "$out" | grep -q "Successfully downloaded"; then
    echo "ALL_OK round=$round"
    break
  fi
  # 两种报错：直接缺失 / 冲突里被依赖的包
  missing=$( { echo "$out" | grep -oE "No matching distribution found for [a-zA-Z0-9_.-]+";
                echo "$out" | grep -oE "depends on [a-zA-Z0-9_.-]+" | awk '{print $3}'; }
              | sort -u )
  [ -z "$missing" ] && { echo "UNKNOWN_ERROR round=$round"; echo "$out" | tail -12; break; }
  echo "round=$round 处理: $missing"
  for pkg in $missing; do
    has_wheel "$pkg" && { echo "  $pkg 已有 wheel，跳过"; continue; }
    # 先下载 sdist
    "$VENV_PY" -m pip download --no-deps --no-binary=:all: -d . \
      -i https://mirrors.aliyun.com/pypi/simple "$pkg" >/dev/null 2>&1 || { echo "  $pkg sdist 下载失败"; continue; }
    sdist=$(ls . | grep -i "^${pkg//-/_}-.*\.\(tar\.gz\|zip\)$" | head -1)
    # 宿主机打 wheel（纯 Python 包会产出 py3-none-any）
    "$VENV_PY" -m pip wheel --no-deps --wheel-dir . "./$sdist" >/dev/null 2>&1
    whl=$(ls . | grep -i "^${pkg//-/_}-.*\.whl$" | head -1)
    if [ -n "$whl" ] && ! echo "$whl" | grep -qE "macosx|darwin"; then
      echo "  $pkg -> $whl (host)"
    else
      # C 扩展包：在 linux py310 容器里打
      [ -n "$whl" ] && rm -f "./$whl"
      docker run --rm -v "$PWD":/w "$PYIMAGE" \
        pip wheel --no-deps --wheel-dir /w "/w/$sdist" >/dev/null 2>&1 \
        && echo "  $pkg -> $(ls . | grep -i "^${pkg//-/_}-.*\.whl$" | head -1) (container)" \
        || echo "  $pkg wheel 构建失败"
    fi
  done
done
ls . | wc -l; du -sh .
