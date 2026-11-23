# =====================================================================
# ASR-Service (FastAPI + FunASR) Makefile
# 常用命令: make help
# =====================================================================
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# ----- 项目配置 -----------------------------------------------------
PROJECT_NAME   := asr-service
VENV           := .venv
VENV_PY        := $(VENV)/bin/python
UV             ?= uv
# 镜像源统一配置在 pyproject.toml [[index]]（阿里云 PyPI 镜像）

PORT           ?= 8001
HOST           ?= 127.0.0.1

APP_MODULE     := app.main:app
UVICORN_FLAGS  := --host $(HOST) --port $(PORT)
DEV_FLAGS      := --reload

# Docker / 镜像
IMAGE_NAME     ?= $(PROJECT_NAME)
# 默认取 git 版本（tag > 短 hash，含未提交改动加 -dirty）；非 git 环境退化为 dev
IMAGE_TAG      ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
DOCKERFILE     ?= Dockerfile.gpu
PLATFORM       ?= linux/amd64

# 工具链（由 uv sync 的 dev 依赖组提供，见 pyproject.toml）
RUFF           := $(VENV)/bin/ruff
PYTEST         := $(VENV)/bin/pytest
FORMAT_TARGETS := app scripts tests

# ----- 帮助 ----------------------------------------------------------
.DEFAULT_GOAL := help
.PHONY: help
help: ## 显示本帮助信息
	@awk 'BEGIN {FS = ":.*## "} \
		/^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2} \
		/^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)}' $(MAKEFILE_LIST)

##@ 环境准备
.PHONY: venv install deps lock
venv: ## 创建虚拟环境（uv 托管 Python，缺失时自动下载）
	$(UV) venv $(VENV)

install: ## 按 uv.lock 同步依赖（含 dev 工具组，可重复执行）
	$(UV) sync
deps: install ## make deps 的别名

lock: ## 重新解析依赖并更新 uv.lock（修改 pyproject.toml 后执行）
	$(UV) lock

.PHONY: models
models: ## 预置 FunASR 模型到 ./models（本地开发首次必跑；生产为运行时挂载）
	test -x $(VENV_PY) || $(MAKE) install
	$(VENV_PY) scripts/download_models.py

##@ 开发调试
.PHONY: run dev serve
run: ## 启动生产式服务（无热重载）
	$(VENV_PY) -m uvicorn $(APP_MODULE) $(UVICORN_FLAGS)

dev: DEV_FLAGS += $(shell [ -f .env ] && echo --env-file .env)
dev: ## 启动开发服务器（热重载）
	$(VENV_PY) -m uvicorn $(APP_MODULE) $(UVICORN_FLAGS) $(DEV_FLAGS)

serve: run ## make serve 的别名

.PHONY: health
health: ## 探活 /health（需先 make run / make dev）
	curl -fsS http://$(HOST):$(PORT)/health | $(VENV_PY) -m json.tool

##@ 测试与质量
.PHONY: test
test: ## 运行冒烟测试（tests/，不存在时做导入冒烟检查）
	@if test -d tests; then \
		test -x $(PYTEST) || $(MAKE) install; \
		$(VENV_PY) -m pytest tests/ -v; \
	else \
		echo "tests/ 不存在，执行导入冒烟检查"; \
		test -x $(VENV_PY) || $(MAKE) install; \
		$(VENV_PY) -c "from app.main import app; print('import OK')"; \
	fi

.PHONY: smoke
smoke: ## 全链路冒烟（真实加载 FunASR 模型跑通提交→转写→查询，约 1 分钟；需先 make models）
	test -x $(PYTEST) || $(MAKE) install
	$(VENV_PY) -m pytest tests/ -m smoke_full -v

.PHONY: lint
lint: ## 静态检查（ruff check）
	test -x $(RUFF) || $(MAKE) install
	$(RUFF) check $(FORMAT_TARGETS)

.PHONY: format
format: ## 自动格式化（ruff format + fix）
	test -x $(RUFF) || $(MAKE) install
	$(RUFF) format $(FORMAT_TARGETS)
	$(RUFF) check --fix $(FORMAT_TARGETS)

##@ 构建
.PHONY: build
build: ## 构建本地 Docker 镜像（IMAGE_NAME/IMAGE_TAG/DOCKERFILE/PLATFORM 可覆盖）
	docker build --platform $(PLATFORM) -f $(DOCKERFILE) -t $(IMAGE_NAME):$(IMAGE_TAG) .

.PHONY: build-cpu
build-cpu: ## 构建 CPU 镜像
	$(MAKE) build DOCKERFILE=Dockerfile.cpu

##@ 离线交付
# 交付包结构（dist/<name>.tar.gz）：镜像 tar + wheels + 离线部署编排/脚本（不含源码与模型）
DIST_DIR  ?= dist
DIST_NAME := $(PROJECT_NAME)-$(IMAGE_TAG)
DIST_ROOT := $(DIST_DIR)/$(DIST_NAME)
DIST_TAR  := $(DIST_DIR)/$(DIST_NAME).tar.gz

# 离线部署用 compose（不 build，直接 load 镜像；模型/上传/DB 走挂载）
define OFFLINE_COMPOSE
version: "3.9"

services:
  asr-service:
    image: $(IMAGE_NAME):$(IMAGE_TAG)
    container_name: asr-service
    restart: unless-stopped
    ports:
      - "8001:8001"
    env_file:
      - .env
    volumes:
      - ./models:/app/models
      - ./uploads:/app/uploads
      - ./asr_service.db:/app/asr_service.db
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8001/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    deploy:
      resources:
        limits:
          memory: 8G
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
endef
export OFFLINE_COMPOSE

define DEPLOY_SH
#!/usr/bin/env bash
# 离线部署：目标机器需已安装 Docker、NVIDIA Container Toolkit 与 docker compose 插件
set -euo pipefail
cd "$$(dirname "$$0")"

docker load -i image-$(IMAGE_TAG).tar.gz
test -f .env || cp .env.example .env   # 部署前请修改 ASR_API_KEY
mkdir -p uploads/asr
touch asr_service.db
test -d models/iic || echo "警告: models/iic 不存在，请单独拷贝 FunASR 模型到 ./models/iic（约 1.5GB）"
docker compose -f docker-compose.offline.yml up -d
docker compose -f docker-compose.offline.yml ps
endef
export DEPLOY_SH

define PACKAGE_README
# $(PROJECT_NAME) 离线交付包（$(IMAGE_TAG) / linux）

## 内容清单
- image-$(IMAGE_TAG).tar.gz：Docker 镜像（docker load 加载，含全部 Python 依赖）
- wheels/：离线重建镜像所需的全部 wheel（可选，仅重建镜像时用）
- docker-compose.offline.yml + deploy.sh：离线部署编排与一键脚本

> 模型与源码不在包内：模型需单独拷贝到 models/iic（约 1.5GB，可运行
> `make models` 下载）；源码请从代码仓库获取。

## 部署步骤
1. 将本目录整体拷贝到目标机器
2. 执行 ./deploy.sh（或按 README 手动 docker load + compose up）
3. 修改 .env 中的 ASR_API_KEY
4. 探活：curl http://localhost:8001/health

> GPU 版需目标机器支持 nvidia-container-toolkit；CPU 环境请用
> `make build-cpu` 重新打包（make package DOCKERFILE=Dockerfile.cpu）。
endef
export PACKAGE_README

.PHONY: package
package: ## 构建离线交付压缩包到 dist/<name>.tar.gz（镜像 tar + wheels + 部署脚本；不含源码与模型）
	$(MAKE) build
	rm -rf "$(DIST_ROOT)"
	mkdir -p "$(DIST_ROOT)"
	docker save $(IMAGE_NAME):$(IMAGE_TAG) | gzip > "$(DIST_ROOT)/image-$(IMAGE_TAG).tar.gz"
	if test -d wheels; then cp -R wheels "$(DIST_ROOT)/"; fi
	cp .env.example "$(DIST_ROOT)/"
	echo "$$OFFLINE_COMPOSE" > "$(DIST_ROOT)/docker-compose.offline.yml"
	echo "$$DEPLOY_SH" > "$(DIST_ROOT)/deploy.sh"
	chmod +x "$(DIST_ROOT)/deploy.sh"
	echo "$$PACKAGE_README" > "$(DIST_ROOT)/README.md"
	tar -C "$(DIST_DIR)" -czf "$(DIST_TAR)" "$(DIST_NAME)"
	rm -rf "$(DIST_ROOT)"
	@echo "交付压缩包已生成: $(DIST_TAR)"
	@du -sh "$(DIST_TAR)" | awk '{printf "  体积: %s\n", $$1}'

##@ 清理
.PHONY: clean clean-models clean-dist clean-all
clean: ## 清理临时产物（缓存/字节码/测试缓存）
	rm -rf .pytest_cache .ruff_cache $(VENV)/.pytest_cache
	find . -path ./$(VENV) -prune -o -type d -name __pycache__ -print0 \
		| xargs -0 rm -rf 2>/dev/null || true

clean-models: ## 删除 ./models 下已下载模型（谨慎：约 1.5GB，需重新下载）
	rm -rf models/iic

clean-dist: ## 删除 dist/ 下的离线交付产物（镜像+模型副本，体积大）
	rm -rf $(DIST_DIR)

clean-all: clean clean-models clean-dist ## 深度清理（= clean + clean-models + clean-dist + 删除虚拟环境）
	rm -rf $(VENV)
