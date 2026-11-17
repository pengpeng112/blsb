"""Create a blank text-formatted workbook for future manual upload comparison."""
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment

HERE = Path(__file__).resolve().parent
HEADERS = ["人工单据号", "业务发生时间", "药房编码或名称", "追溯码（文本）",
           "人工上报时间", "人工上报结果（原值）", "平台单据号", "业务类型（原值）",
           "数量（可选）", "退药标记（原值）", "拆零标记（原值）", "备注"]


def main():
    path = HERE / "人工上报核对模板.xlsx"
    if path.exists():
        raise RuntimeError("template exists; do not overwrite a filled user file")
    wb = Workbook()
    ws = wb.active
    ws.title = "人工上报明细"
    ws.append(HEADERS)
    for row in ws.iter_rows(min_row=2, max_row=1001, max_col=len(HEADERS)):
        for cell in row:
            cell.number_format = "@"
    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="24577A")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:L1001"
    widths = [24, 25, 24, 30, 25, 27, 28, 24, 20, 24, 24, 45]
    for col, width in zip("ABCDEFGHIJKL", widths):
        ws.column_dimensions[col].width = width
    notes = wb.create_sheet("填写说明")
    instructions = [
        ("用途", "仅整理人工历史记录供只读比对，不是发送文件，不会自动上报。"),
        ("现成文件", "有原始Excel/CSV直接提供即可，不要求重新填模板或改列名。"),
        ("最有帮助的字段", "人工单据号、业务发生时间、药房编码或名称、完整追溯码。"),
        ("一行一码", "同一单据多个码可以分多行；原文件一格多码也可原样保留，先确认分隔规则。"),
        ("文本格式", "模板数据格已设文本。追溯码和单据号不得转数字，保留前导零和完整位数。"),
        ("时间", "业务发生时间和人工上报时间分开填，建议yyyy-mm-dd hh:mm:ss，并说明来源时区。"),
        ("原始状态", "业务类型/结果/退药/拆零均保留原值，未知留空，不猜编码。"),
        ("数量", "可选；有则保留原值和单位说明，缺失不编造。"),
        ("原始行", "请保留原文件/工作表/原行号关系；比对时自动记录，不先去重删除。"),
        ("患者字段", "不需要姓名、身份证、电话、地址等患者个人信息。"),
        ("空白行", "模板含1000行预设文本格式的空白行，比对统计时忽略全空行。"),
        ("结果", "区分一致、人工有视图无、视图有人工无、码/次数差异、时点状态变化及无法判定。"),
    ]
    for row in instructions:
        notes.append(row)
    notes.column_dimensions["A"].width = 24
    notes.column_dimensions["B"].width = 110
    for row in notes:
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)
    read = load_workbook(path)
    assert read["人工上报明细"]["D2"].number_format == "@"
    assert all(c.value is None for row in read["人工上报明细"].iter_rows(min_row=2) for c in row)
    assert [c.value for c in read["人工上报明细"][1]] == HEADERS
    read.close()
    print("Blank comparison workbook created and verified; no patient or trace data included.")


if __name__ == "__main__":
    main()
