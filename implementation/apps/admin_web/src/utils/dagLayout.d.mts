// dagLayout.mjs 类型声明。
export interface DagNode {
  id: string, type: string, label: string, full: string,
  x: number, y: number, layer: number, isolated: boolean
}
export interface DagEdge {
  from: string, to: string, on: string,
  x1: number, y1: number, x2: number, y2: number
}
export interface DagLayout {
  nodes: DagNode[], edges: DagEdge[], layers: number, errors: string[]
}
export function layoutDag(dsl: unknown): DagLayout
export function stateFill(state?: string | null): { fill: string, stroke: string }
