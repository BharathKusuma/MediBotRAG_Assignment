import os
import functools
from typing import Callable, Any

# Configure LangSmith environment defaults if present
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "true").lower() == "true"
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "medibot-rag-evaluation")
os.environ["LANGSMITH_PROJECT"] = LANGSMITH_PROJECT

try:
    from langsmith import traceable as ls_traceable
    HAS_LANGSMITH = True
except ImportError:
    HAS_LANGSMITH = False
    ls_traceable = None

def traceable(name: str = None, run_type: str = "chain", **kwargs):
    """
    Decorator that applies LangSmith tracing if available and configured,
    or transparently passes through if LangSmith is unconfigured.
    """
    def decorator(fn: Callable) -> Callable:
        if HAS_LANGSMITH and ls_traceable is not None and os.getenv("LANGSMITH_API_KEY"):
            return ls_traceable(name=name or fn.__name__, run_type=run_type, **kwargs)(fn)
        
        # Transparent wrapper when LangSmith is not active
        @functools.wraps(fn)
        def wrapper(*args, **call_kwargs):
            return fn(*args, **call_kwargs)
        return wrapper
    return decorator
