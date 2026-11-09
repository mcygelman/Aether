"""Backends (MOD-07).

Model runtimes behind one inference contract: LM Studio, llama.cpp, the DeepSeek API, Ollama later; vendor dialects, deployments and metering.

Contract: Inference contract (request, response, usage); dialect normalization and deployment dialect selection (the deployment records themselves are the Model Catalog's); request measurement and metering.
Depends on: models, credentials, config, integrity.
Known debt, kept on purpose (2026-10-08): model.py's send path imports four Runtime names lazily
(ConfiguredRequestAttempt, DispatchAttempt, GenerationRequestAttempt, native normal completion).
They are exact-type authority checks on who owns a request; a registration hook would weaken
them. The request-budget client and the deployment package moved to the Runtime the same day.
Owner: Operator assigns.

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('admission_receipts', 'charter_transport', 'configured_accounting', 'deployment_binding', 'deployment_probe', 'deployment_profiles', 'deployment_transport', 'dialect', 'inference_contract', 'legacy_endpoints', 'llama_server', 'lmstudio_counter', 'model', 'model_settings_http', 'monetary', 'profile_client', 'qwen38_renderer')
_NAMES = {'InferenceRequest': 'inference_contract', 'InferenceResponse': 'inference_contract', 'ContractError': 'inference_contract'}

# An export that leaves some submodules out (the public repository) lists only those it contains.
_HERE = __import__("pathlib").Path(__file__).parent
_PRESENT = lambda m: (_HERE / (m.replace(".", "/") + ".py")).is_file() or (_HERE / m.replace(".", "/") / "__init__.py").is_file()
_SUBMODULES = tuple(m for m in _SUBMODULES if _PRESENT(m))
_NAMES = {name: module for name, module in _NAMES.items() if _PRESENT(module)}

__all__ = sorted(set(_SUBMODULES) | set(_NAMES))


def __getattr__(name):
    if name in _SUBMODULES:
        return _import_module(f"{__name__}.{name}")
    if name in _NAMES:
        return getattr(_import_module(f"{__name__}.{_NAMES[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return __all__
