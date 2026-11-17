"""Export an allowlisted Markdown handoff for chat-only models; no raw data/logs."""
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parents[1]
FILES = ("AGENTS.md", "docs/ai/STATE.md", "docs/ai/HANDOFF.md", "docs/ai/BACKLOG.md")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=ROOT / ".ai" / "context-packet.md")
    args = ap.parse_args()
    target = args.output.resolve()
    if target.parent != (ROOT / ".ai").resolve() or target.suffix.lower() != ".md":
        ap.error("output must be a Markdown file directly inside .ai/; cannot overwrite project files or session registry")
    lines = ["# BLYB 跨工具接手包", "", "内容是生成时的快照；接手前核对工作区最新状态。",
             "聊天模型不能访问本地文件时，只能给补丁/建议，不得声称已修改、已运行或已验收。",
             ""]
    for name in FILES:
        lines.extend([f"## 来源：{name}", "", (ROOT / name).read_text(encoding="utf-8"), ""])
    target.parent.mkdir(exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {target.relative_to(ROOT)} ({target.stat().st_size} bytes); review before sharing")


if __name__ == "__main__":
    main()
