from __future__ import annotations

import pyray as rl

from openpilot.common.params import Params
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget, DialogResult
from openpilot.system.ui.widgets.list_view import ListItem, ItemAction, button_item
from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog
from openpilot.system.ui.widgets.scroller import Scroller
from openpilot.system.ui.widgets.toggle import Toggle, WIDTH as TOGGLE_WIDTH, HEIGHT as TOGGLE_HEIGHT


class ParamToggleAction(ItemAction):
  def __init__(self, params: Params, key: str):
    super().__init__(width=TOGGLE_WIDTH, enabled=True)
    self._params = params
    self._key = key
    self._toggle = Toggle(initial_state=params.get_bool(key))
    # Disable Toggle's internal click handling; this action owns clicks.
    self._toggle.set_touch_valid_callback(lambda: False)

  def _handle_mouse_release(self, _mouse_pos):
    new_state = not self._params.get_bool(self._key)
    self._params.put_bool(self._key, new_state)
    self._toggle.set_state(new_state)

  def _render(self, rect: rl.Rectangle) -> bool:
    state = self._params.get_bool(self._key)
    self._toggle.set_enabled(self.enabled)
    self._toggle.set_state(state)
    self._toggle.render(rl.Rectangle(rect.x, rect.y + (rect.height - TOGGLE_HEIGHT) / 2, TOGGLE_WIDTH, TOGGLE_HEIGHT))
    return False


def param_toggle_item(title: str, description: str, params: Params, key: str) -> ListItem:
  return ListItem(title=title, description=description, action_item=ParamToggleAction(params, key))


class TeslaLayout(Widget):
  def __init__(self):
    super().__init__()
    self._params = Params()
    self._dialog: MultiOptionDialog | None = None
    self._dialog_handler = None

    self._items = [
      button_item("Follow Distance", self._follow_distance_text, self._follow_distance_desc(), callback=self._show_follow_distance),
      button_item("Hands On Level", self._hands_on_text, self._hands_on_desc(), callback=self._show_hands_on_level),
      button_item("Radar Offset", self._radar_offset_text, self._radar_offset_desc(), callback=self._show_radar_offset),

      param_toggle_item("Radar Upside Down", self._radar_upside_down_desc(), self._params, "TinklaUseTeslaRadarUpsideDown"),
      param_toggle_item("Ignore Radar SGU Error", self._radar_sgu_ignore_desc(), self._params, "TinklaTeslaRadarIgnoreSGUError"),
      param_toggle_item("Ignore Stock AEB", self._ignore_aeb_desc(), self._params, "TinklaIgnoreStockAeb"),
      param_toggle_item("Autopilot Disabled", self._autopilot_disabled_desc(), self._params, "TinklaAutopilotDisabled"),
      param_toggle_item("Disable Engage/Disengage Sounds", self._mute_start_stop_desc(), self._params, "TinklaDisableStartStopSounds"),
      param_toggle_item("Disable Prompt Sounds", self._mute_prompt_desc(), self._params, "TinklaDisablePromptSounds"),
    ]

    self._scroller = Scroller(self._items, line_separator=True, spacing=0)

  def _render(self, rect):
    self._scroller.render(rect)

  def _get_float(self, key: str, default: float) -> float:
    val = self._params.get(key, return_default=True)
    try:
      if isinstance(val, (bytes, bytearray)):
        val = val.decode()
      return float(val)
    except (TypeError, ValueError):
      return float(default)

  def _follow_distance_desc(self) -> str:
    return "Override Tesla follow time used by openpilot longitudinal control."

  def _hands_on_desc(self) -> str:
    return "Hands-on steering torque threshold level for engagement."

  def _radar_offset_desc(self) -> str:
    return "Lateral offset applied to radar tracks."

  def _radar_upside_down_desc(self) -> str:
    return "Invert radar lateral axis (for upside-down mounting)."

  def _radar_sgu_ignore_desc(self) -> str:
    return "Ignore radar SGU hardware-fail errors."

  def _ignore_aeb_desc(self) -> str:
    return "Ignore stock AEB events reported by the vehicle."

  def _autopilot_disabled_desc(self) -> str:
    return "Lateral-only mode: disables openpilot longitudinal and allows steering when Tesla Autopilot is disabled (useful below ~18 mph)."

  def _mute_start_stop_desc(self) -> str:
    return "Disable engage/disengage sounds."

  def _mute_prompt_desc(self) -> str:
    return "Disable prompt sounds."

  def _follow_distance_text(self) -> str:
    val = self._get_float("TinklaFollowDistance", 1.45)
    return f"{val:.2f} s"

  def _hands_on_text(self) -> str:
    val = self._get_float("TinklaHandsOnLevel", 2.0)
    return f"{int(round(val))} lvl"

  def _radar_offset_text(self) -> str:
    val = self._get_float("TinklaRadarOffset", 0.0)
    return f"{val:+.1f} m"

  def _open_dialog(self, title: str, options: list[str], current: str, on_confirm):
    self._dialog = MultiOptionDialog(title, options, current=current)
    self._dialog_handler = on_confirm
    gui_app.set_modal_overlay(self._dialog, callback=self._handle_dialog)

  def _handle_dialog(self, result: int):
    if result == DialogResult.CONFIRM and self._dialog and self._dialog_handler:
      self._dialog_handler(self._dialog.selection)
    self._dialog = None
    self._dialog_handler = None

  def _show_follow_distance(self):
    options = [f"{v:.2f} s" for v in (0.90, 1.15, 1.45, 1.75, 2.00)]
    self._open_dialog("Follow Distance", options, self._follow_distance_text(), self._set_follow_distance)

  def _set_follow_distance(self, selection: str):
    try:
      val = float(selection.split()[0])
    except (ValueError, IndexError):
      return
    self._params.put("TinklaFollowDistance", f"{val:.2f}")

  def _show_hands_on_level(self):
    options = [f"{v} lvl" for v in (1, 2, 3)]
    self._open_dialog("Hands On Level", options, self._hands_on_text(), self._set_hands_on_level)

  def _set_hands_on_level(self, selection: str):
    try:
      val = int(selection.split()[0])
    except (ValueError, IndexError):
      return
    val = max(1, min(3, val))
    self._params.put("TinklaHandsOnLevel", f"{float(val):.1f}")

  def _show_radar_offset(self):
    values = [round(x * 0.1, 1) for x in range(-10, 11)]
    options = [f"{v:+.1f} m" for v in values]
    self._open_dialog("Radar Offset", options, self._radar_offset_text(), self._set_radar_offset)

  def _set_radar_offset(self, selection: str):
    try:
      val = float(selection.split()[0])
    except (ValueError, IndexError):
      return
    val = max(-1.0, min(1.0, val))
    self._params.put("TinklaRadarOffset", f"{val:+.1f}")
