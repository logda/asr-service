# =====================================================================
# ASR-Service (FastAPI + FunASR) Makefile
# 常用命令: make help
# =====================================================================
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# ----- 项目配置 -----------------------------------------------------
PROJECT_NAME   := asr-service
PYTHON         ?= python3.12
VENV           := .venv
VENV_PY        := $(VENV)/bin/python
PIP            := $(VENV_PY) -m pip
PIP_FLAGS       := -i https://mirrors.aliyun.com/pypi/simple
export PIP_DISABLE_PIP_VERSION_CHECK := 1

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

# 工具链（按需用 pip 安装，目标中自动兜底）
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
.PHONY: venv install deps
venv: ## 创建 Python 3.12 虚拟环境（funasr 1.3.1 兼容版本）
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip --version >/dev/null 2>&1 || $(VENV_PY) -m ensurepip --upgrade
	$(PIP) install $(PIP_FLAGS) --upgrade pip

install: venv install-deps ## 安装依赖（= venv + install-deps，可重复执行）
deps: install ## make deps 的别名

.PHONY: install-deps
install-deps: ## 仅安装 requirements.txt 依赖到已有虚拟环境
	test -x $(VENV_PY) || $(MAKE) venv
	$(VENV_PY) -m pip --version >/dev/null 2>&1 || $(VENV_PY) -m ensurepip --upgrade
	$(PIP) install $(PIP_FLAGS) -r requirements.txt
	$(PIP) install $(PIP_FLAGS) ruff pytest || true

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
		test -x $(PYTEST) || $(PIP) install $(PIP_FLAGS) pytest; \
		$(VENV_PY) -c "import httpx" >/dev/null 2>&1 || $(PIP) install $(PIP_FLAGS) httpx; \
		$(VENV_PY) -m pytest tests/ -v; \
	else \
		echo "tests/ 不存在，执行导入冒烟检查"; \
		$(VENV_PY) -c "from app.main import app; print('import OK')"; \
	fi

.PHONY: smoke
smoke: ## 全链路冒烟（真实加载 FunASR 模型跑通提交→转写→查询，约 1 分钟；需先 make models）
	test -x $(PYTEST) || $(PIP) install $(PIP_FLAGS) pytest
	$(VENV_PY) -c "import httpx" >/dev/null 2>&1 || $(PIP) install $(PIP_FLAGS) httpx
	$(VENV_PY) -m pytest tests/ -m smoke_full -v

.PHONY: lint
lint: ## 静态检查（ruff check）
	test -x $(RUFF) || $(PIP) install $(PIP_FLAGS) ruff
	$(RUFF) check $(FORMAT_TARGETS)

.PHONY: format
format: ## 自动格式化（ruff format + fix）
	test -x $(RUFF) || $(PIP) install $(PIP_FLAGS) ruff
	$(RUFF) format $(FORMAT_TARGETS)
	$(RUFF) check --fix $(FORMAT_TARGETS)

##@ 构建
.PHONY: build
build: ## 构建本地 Docker 镜像（IMAGE_NAME/IMAGE_TAG/DOCKERFILE/PLATFORM 可覆盖）
	docker build --platform $(PLATFORM) -f $(DOCKERFILE) -t $(IMAGE_NAME):$(IMAGE_TAG) .

.PHONY: build-cpu
build-cpu: ## 构建 CPU 镜像
	$(MAKE) build DOCKERFILE=Dockerfile.cpu

##@ 清理
.PHONY: clean clean-models clean-all
clean: ## 清理临时产物（缓存/字节码/测试缓存）
	rm -rf .pytest_cache .ruff_cache $(VENV)/.pytest_cache
	find . -path ./$(VENV) -prune -o -type d -name __pycache__ -print0 \
		| xargs -0 rm -rf 2>/dev/null || true

clean-models: ## 删除 ./models 下已下载模型（谨慎：约 1.5GB，需重新下载）
	rm -rf models/iic

clean-all: clean clean-models ## 深度清理（= clean + clean-models + 删除虚拟环境）
	rm -rf $(VENV)
