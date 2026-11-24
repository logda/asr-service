"""说话人聚类入口的 umap 兼容补丁回归测试（不加载模型、不跑真实 umap）。

FunASR 1.3.1 把 torch 张量直接传给 UMAP；umap-learn 0.6.0 会对 X.dtype 调
np.issubdtype，torch.float32 不是 numpy dtype 而抛 TypeError。该路径仅在
embedding 数 >= 2048（长音频）时触发，普通冒烟覆盖不到，这里用假 UMAP 复刻
该行为并验证 funasr_runtime 导入时的补丁已把张量转为 numpy。
"""

import numpy as np
import pytest
import torch
from funasr.models.campplus import cluster_backend
from funasr.models.campplus.cluster_backend import ClusterBackend, UmapHdbscan

import app.services.funasr_runtime  # noqa: F401  导入即应用补丁

# ClusterBackend.forward 的 umap 路径阈值：N < 2048 走 spectral 聚类
UMAP_MIN_ROWS = 2048


class _FakeUMAP:
    """复刻 umap-learn 0.6.0 fit_transform 的 dtype 检查，不做真实降维。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.input = None

    def fit_transform(self, X):
        self.input = X
        # 与 umap_.py 0.6.0 一致：非 numpy dtype 在这里抛 TypeError
        input_dtype = getattr(X, "dtype", None)
        if input_dtype is not None:
            np.issubdtype(input_dtype, np.floating)
        return np.zeros((X.shape[0], self.kwargs.get("n_components", 2)), dtype=np.float32)


class _FakeHDBSCAN:
    def __init__(self, **kwargs):
        pass

    def fit_predict(self, X):
        return np.zeros(X.shape[0], dtype="int")


@pytest.fixture
def fake_umap(monkeypatch):
    instances = []

    def _factory(**kwargs):
        inst = _FakeUMAP(**kwargs)
        instances.append(inst)
        return inst

    import umap.umap_ as umap_module

    monkeypatch.setattr(umap_module, "UMAP", _factory)
    monkeypatch.setattr(cluster_backend, "HDBSCAN", _FakeHDBSCAN)
    return instances


def test_raw_tensor_reproduces_production_error(fake_umap):
    """对照组：未经补丁转换的 torch 张量进入 UMAP -> 与生产一致的 TypeError。"""
    X = torch.randn(UMAP_MIN_ROWS, 80)
    with pytest.raises(TypeError, match="Cannot interpret 'torch.float32'"):
        _FakeUMAP(n_components=10, metric="cosine").fit_transform(X)


def test_umap_hdbscan_patch_converts_tensor_to_numpy(fake_umap):
    X = torch.randn(UMAP_MIN_ROWS, 80)
    labels = UmapHdbscan()(X)

    assert len(fake_umap) == 1
    assert isinstance(fake_umap[0].input, np.ndarray)
    assert fake_umap[0].input.dtype == np.float32
    assert labels.shape == (UMAP_MIN_ROWS,)


def test_cluster_backend_umap_path_accepts_tensor(fake_umap):
    """完整复现生产路径：N >= 2048 的 torch 张量经 ClusterBackend 不再抛错。"""
    torch.manual_seed(0)
    X = torch.randn(UMAP_MIN_ROWS + 52, 80)
    labels = ClusterBackend()(X)

    assert labels.shape == (UMAP_MIN_ROWS + 52,)
    assert len(fake_umap) == 1
    assert isinstance(fake_umap[0].input, np.ndarray)


def test_patch_is_idempotent():
    """重复导入模块不会叠加多层包装（_asr_numpy_input 标记阻止二次包裹）。"""
    import importlib

    before = UmapHdbscan.__call__
    importlib.reload(importlib.import_module("app.services.funasr_runtime"))

    assert getattr(before, "_asr_numpy_input", False) is True
    assert UmapHdbscan.__call__ is before
