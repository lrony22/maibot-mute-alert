"""按 Host 的包加载方式导入插件，所有测试使用真实 SDK。"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sys

root = Path(__file__).resolve().parents[1]
spec = spec_from_file_location("mute_alert", root / "plugin.py", submodule_search_locations=[str(root)])
plugin_module = module_from_spec(spec)
sys.modules[spec.name] = plugin_module
spec.loader.exec_module(plugin_module)

MuteAlertPlugin = plugin_module.MuteAlertPlugin
create_plugin = plugin_module.create_plugin
MuteAlertConfig = sys.modules["mute_alert.config"].MuteAlertConfig
MuteEvent = sys.modules["mute_alert.detector"].MuteEvent
parse_mute_event = sys.modules["mute_alert.detector"].parse_mute_event
MuteNotifier = sys.modules["mute_alert.notifier"].MuteNotifier
format_duration = sys.modules["mute_alert.notifier"].format_duration
