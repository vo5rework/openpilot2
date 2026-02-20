"""Config helpers backed by Params (Unity compatibility shim)."""
from __future__ import annotations

try:
  from openpilot.common.params import Params
except ImportError:  # pragma: no cover
  from common.params import Params

_P = Params()

def save_bool_param(param: str, val: bool) -> None:
  _P.put_bool(param, bool(val))

def load_bool_param(param: str, default_val: bool) -> bool:
  if _P.get(param) is None:
    _P.put_bool(param, bool(default_val))
    return bool(default_val)
  return _P.get_bool(param)

def save_float_param(param: str, val: float) -> None:
  _P.put(param, str(float(val)))

def load_float_param(param: str, default_val: float) -> float:
  v = _P.get(param)
  if v is None:
    _P.put(param, str(float(default_val)))
    return float(default_val)
  try:
    return float(v)
  except Exception:
    _P.put(param, str(float(default_val)))
    return float(default_val)

def save_str_param(param: str, val: str) -> None:
  _P.put(param, str(val))

def load_str_param(param: str, default_val: str) -> str:
  v = _P.get(param)
  if v is None:
    _P.put(param, str(default_val))
    return str(default_val)
  try:
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
  except Exception:
    _P.put(param, str(default_val))
    return str(default_val)
