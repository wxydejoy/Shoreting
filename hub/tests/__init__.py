"""测试包级安全网：单元测试绝不许碰真机器。

历史教训：test_focus 有个用例漏了 mock（test_scene_error_does_not_crash），
focus 默认 mute=True，跑一遍全量测试就把 Mac 的系统音量 osascript 成 0。
这里在 service 还没被 import 之前把 osascript 出口整个换成桩——以后任何
测试再漏 mock，动的也只是桩，不是音量 mixer。真实行为由各测试里的
实例桩（hub._read_volume / _write_volume）覆盖。

注意顺序依赖：unittest 先 import tests 包再 import 测试模块，所以这里的
替换一定发生在 dock_hub.service 绑定 `from ... import write_output_volume`
之前。别把这段挪到别的测试模块里去。
"""

from dock_hub import system_volume as _sv

_WRITES: list[int] = []


def _fake_read_output_volume() -> int:
    return 40


def _fake_write_output_volume(level: int) -> None:
    _WRITES.append(int(level))


_sv.read_output_volume = _fake_read_output_volume  # type: ignore[assignment]
_sv.write_output_volume = _fake_write_output_volume  # type: ignore[assignment]
