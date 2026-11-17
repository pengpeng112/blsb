"""流程DSL校验与执行（05 §2）。有限DAG；发布前验证环、端口、未配置节点。"""
from __future__ import annotations

from dataclasses import dataclass

from contracts import new_uuid
from core_db.engine import utcnow
from core_db.models import StepRun, WorkflowRun

# node_type: (输入端口kind集合, 输出kind, 是否有外部副作用)
NODE_SPECS: dict[str, tuple[set[str], str, bool]] = {
    "source.receive": ({"event"}, "raw", False),
    "source.poll": (set(), "raw", False),
    "parse": ({"raw"}, "typed", False),
    "normalize": ({"typed"}, "canonical", False),
    "resolve": ({"canonical", "raw"}, "resolved", False),
    "validate": ({"resolved", "canonical", "typed"}, "quality", False),
    "wait_dependencies": ({"quality", "resolved"}, "ready", False),
    "assets.ensure": ({"ready", "resolved"}, "assets", False),
    "format.convert": ({"assets"}, "assets", True),
    "package.freeze": ({"ready", "assets", "quality"}, "package", True),
    "target.map": ({"package"}, "mapped", False),
    "target.validate": ({"mapped"}, "validated", False),
    "deliver.push": ({"validated"}, "sent", True),
    "deliver.publish": ({"package", "validated"}, "publication", True),
    "receipt.wait": ({"sent", "publication"}, "confirmed", False),
    "reconcile": ({"sent", "publication", "confirmed"}, "reconciled", True),
    "notify": ({"quality", "issue"}, "notified", False),
}

EDGE_STATE_MAP = {
    # 边条件 -> 满足该条件的前驱步骤状态集合（R18：所有控制边参与依赖模型）
    "success": {"SUCCEEDED"},
    "needs_review": {"NEEDS_REVIEW", "WAITING_HUMAN"},
    "invalid": {"FAILED"},
}
TERMINAL_STEP_STATES = {"SUCCEEDED", "FAILED", "NEEDS_REVIEW", "SKIPPED",
                        "CANCELLED"}
_BLOCKING_STEP_STATES = ("WAITING_EXTERNAL", "WAITING_HUMAN", "RUNNING", "READY",
                         "PENDING")


def edge_satisfied(on: str, pred_state: str) -> bool:
    return pred_state in EDGE_STATE_MAP.get(on, EDGE_STATE_MAP["success"])


def validate_dsl(dsl: dict) -> list[str]:
    errors: list[str] = []
    nodes = dsl.get("nodes") or []
    edges = dsl.get("edges") or []
    if not nodes:
        return ["workflow has no nodes"]
    ids = set()
    for n in nodes:
        nid = n.get("id")
        if not nid:
            errors.append("node missing id")
            continue
        if nid in ids:
            errors.append(f"duplicate node id {nid}")
        ids.add(nid)
        if n.get("type") not in NODE_SPECS:
            errors.append(f"node {nid}: unknown type {n.get('type')!r}")
        if not n.get("plugin_ref") and n.get("type") not in NODE_SPECS:
            errors.append(f"node {nid}: missing plugin_ref")
    for e in edges:
        f, t = e.get("from"), e.get("to")
        if f not in ids:
            errors.append(f"edge from unknown node {f!r}")
        if t not in ids:
            errors.append(f"edge to unknown node {t!r}")
        if e.get("on", "success") not in EDGE_STATE_MAP:
            errors.append(f"edge {f}->{t}: invalid on={e.get('on')!r}")
    # 端口兼容
    by_id = {n["id"]: n for n in nodes if n.get("id")}
    for e in edges:
        f, t = e.get("from"), e.get("to")
        if f in by_id and t in by_id:
            ft, tt = by_id[f].get("type"), by_id[t].get("type")
            if ft in NODE_SPECS and tt in NODE_SPECS:
                out_kind = NODE_SPECS[ft][1]
                in_kinds = NODE_SPECS[tt][0]
                if out_kind not in in_kinds:
                    errors.append(
                        f"edge {f}({ft}:{out_kind}) -> {t}({tt}) port incompatible; "
                        f"{t} accepts {sorted(in_kinds)}")
    # 环检测（DFS）
    graph: dict[str, list[str]] = {}
    for e in edges:
        graph.setdefault(e.get("from"), []).append(e.get("to"))
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in ids}
    cycle_at = []

    def dfs(u, stack):
        color[u] = GRAY
        for v in graph.get(u, []):
            if v not in color:
                continue
            if color[v] == GRAY:
                cycle_at.append("->".join(stack + [v]))
            elif color[v] == WHITE:
                dfs(v, stack + [v])
        color[u] = BLACK

    for nid in ids:
        if color[nid] == WHITE:
            dfs(nid, [nid])
    for c in cycle_at:
        errors.append(f"cycle detected: {c}")
    # 孤立节点
    referenced = {e.get("from") for e in edges} | {e.get("to") for e in edges}
    for nid in sorted(ids - referenced):
        if len(ids) > 1:
            errors.append(f"node {nid} is isolated (no edges)")
    return errors


def topo_order(dsl: dict) -> list[str]:
    """排序包含全部节点（控制边只影响调度条件，不影响拓扑位置）。"""
    nodes = [n["id"] for n in dsl["nodes"]]
    indeg = {n: 0 for n in nodes}
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for e in dsl.get("edges", []):
        if e["from"] in adj and e["to"] in indeg:
            adj[e["from"]].append(e["to"])
            indeg[e["to"]] += 1
    queue = sorted([n for n in nodes if indeg[n] == 0])
    out: list[str] = []
    while queue:
        u = queue.pop(0)
        out.append(u)
        for v in sorted(adj[u]):
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    return out


@dataclass
class StepOutcome:
    state: str                  # SUCCEEDED / WAITING_EXTERNAL / WAITING_HUMAN / FAILED / NEEDS_REVIEW
    outputs: dict | None = None
    wait_reason: str | None = None
    error: dict | None = None
    retry_after: int | None = None


def create_run(db, tenant_id: str, workflow_revision_id: str, *, trigger: str,
               context: dict, target_id: str | None = None,
               dry_run: bool = False) -> WorkflowRun:
    run = WorkflowRun(id=new_uuid(), tenant_id=tenant_id,
                      workflow_revision_id=workflow_revision_id,
                      target_id=target_id, trigger=trigger, dry_run=dry_run,
                      state="RUNNING", context=context)
    db.add(run)
    db.flush()
    return run


def materialize_steps(db, run: WorkflowRun, dsl: dict) -> None:
    for nid in topo_order(dsl):
        db.add(StepRun(id=new_uuid(), tenant_id=run.tenant_id, run_id=run.id,
                       node_id=nid, state="PENDING", input_refs=dict(run.context or {})))
    db.flush()


def runnable_steps(db, run: WorkflowRun, dsl: dict):
    """所有控制边参与依赖模型（R18）：
    - 节点READY当且仅当每条入边的前驱状态满足该边的 on 条件；
    - 前驱已终态但条件不满足（未选分支）→ 本节点 SKIPPED（可级联传播）；
    - 前驱等待/运行中 → 本节点保持 PENDING，不可被当根节点调度。"""
    steps = db.query(StepRun).filter_by(run_id=run.id).all()
    by_node = {s.node_id: s for s in steps}
    order = topo_order(dsl)
    preds_of: dict[str, list[tuple[str, str]]] = {s.node_id: [] for s in steps}
    for e in dsl.get("edges", []):
        if e.get("to") in preds_of:
            preds_of[e["to"]].append((e["from"], e.get("on", "success")))
    ready = []
    changed = True
    while changed:  # 迭代传播 SKIPPED
        changed = False
        for s in steps:
            if s.state != "PENDING":
                continue
            preds = preds_of.get(s.node_id, [])
            if not preds:
                if s.state == "PENDING":
                    s.state = "READY"
                    ready.append(s)
                continue
            undecided = False
            skip = False
            for p_node, on in preds:
                ps = by_node.get(p_node)
                if ps is None:
                    continue
                if ps.state in _BLOCKING_STEP_STATES:
                    undecided = True
                    break
                if not edge_satisfied(on, ps.state):
                    skip = True  # 分支未激活/前驱SKIPPED
            if undecided:
                continue
            if skip:
                s.state = "SKIPPED"
                s.updated_at = utcnow()
                changed = True
            else:
                s.state = "READY"
                ready.append(s)
    db.flush()
    return ready, order


def run_finished(db, run: WorkflowRun, dsl: dict) -> str:
    """汇总run状态：任一FAILED→FAILED；等待类未终→WAITING；NEEDS_REVIEW→
    WAITING_HUMAN；其余全终态→SUCCEEDED（SKIPPED不算失败）。"""
    steps = db.query(StepRun).filter_by(run_id=run.id).all()
    if any(s.state == "FAILED" for s in steps):
        run.state = "FAILED"
    elif any(s.state in ("WAITING_EXTERNAL", "WAITING_HUMAN", "PENDING", "READY",
                         "RUNNING") for s in steps):
        run.state = "WAITING"
    elif any(s.state == "NEEDS_REVIEW" for s in steps):
        run.state = "NEEDS_REVIEW"
    else:
        run.state = "SUCCEEDED"
    run.finished_at = utcnow() if run.state not in ("WAITING",) else None
    db.flush()
    return run.state
