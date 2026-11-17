// RT-05 dagLayout 纯函数：类型化 re-export（唯一事实源 dagLayout.mjs；node:test 直测 mjs）。

import { layoutDag as _layoutDag, stateFill as _stateFill } from './dagLayout.mjs'

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

export function layoutDag(dsl: unknown): DagLayout {
  return _layoutDag(dsl) as DagLayout
}
export function stateFill(state?: string | null): { fill: string, stroke: string } {
  return _stateFill(state)
}
