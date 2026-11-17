"""RT-06 一次性补丁：迁移旧脚手架测试到 F01 契约（用后即删）。"""
BS = chr(92)
p = 'tests/unit/test_new_report_target_script.py'
s = open(p, encoding='utf-8').read()

old_line = '("_SEEDS = [' + BS + 'n    ]' + BS + 'n")'
new_line = '("def _seed_rows():' + BS + 'n    return [' + BS + 'n    ]' + BS + 'n")'
assert old_line in s, 'seed line'
s = s.replace(old_line, new_line)

old_assert = ('assert \'"code": "skel_test_jn"\' in rt and '
              '"new_report_target seed" in rt')
new_assert = ("assert \"'code': 'skel_test_jn'\" in rt and "
              "\"scaffold:skel_test_jn\" in rt\n"
              "    import ast as _ast\n"
              "    _ast.parse(rt)")
assert old_assert in s, 'marker assert'
s = s.replace(old_assert, new_assert)

open(p, 'w', encoding='utf-8', newline='\n').write(s)
print('migrated ok')
