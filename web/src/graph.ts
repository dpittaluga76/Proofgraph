export const NODE_KINDS = [
  "goal",
  "constraint",
  "strategy",
  "source",
  "claim",
  "opportunity",
  "assumption",
  "risk",
  "validation_experiment",
  "generation_placeholder",
] as const;

export type NodeKind = (typeof NODE_KINDS)[number];

export const CREATABLE_NODE_KINDS = [
  "goal",
  "constraint",
  "strategy",
  "claim",
  "opportunity",
  "assumption",
  "risk",
  "validation_experiment",
] as const satisfies readonly NodeKind[];

export const NODE_KIND_LABELS: Record<NodeKind, string> = {
  goal: "Goal",
  constraint: "Constraint",
  strategy: "Strategy",
  source: "Source",
  claim: "Claim",
  opportunity: "Opportunity",
  assumption: "Assumption",
  risk: "Risk",
  validation_experiment: "Validation experiment",
  generation_placeholder: "Generation in progress",
};

export const EDGE_KINDS = [
  "supports",
  "contradicts",
  "derived_from",
  "constrained_by",
  "evolves_into",
  "requires_validation",
  "extracted_from",
] as const;

export type EdgeKind = (typeof EDGE_KINDS)[number];

export const EDGE_KIND_LABELS: Record<EdgeKind, string> = {
  supports: "Supports",
  contradicts: "Contradicts",
  derived_from: "Derived from",
  constrained_by: "Constrained by",
  evolves_into: "Evolves into",
  requires_validation: "Requires validation",
  extracted_from: "Extracted from",
};

export type Position = { x: number; y: number };

export type GraphSurfaceSize = { width: number; height: number };

export const MIN_GRAPH_SURFACE_SIZE: GraphSurfaceSize = {
  width: 1500,
  height: 900,
};

const GRAPH_NODE_WIDTH = 244;
const GRAPH_NODE_HEIGHT = 116;
const GRAPH_SURFACE_PADDING = 24;
const GRAPH_SURFACE_CONTENT_MARGIN = 48;

export type GraphNode = {
  id: string;
  canvas_id: string;
  kind: NodeKind;
  title: string;
  body: string | null;
  metadata: Record<string, unknown>;
  branch_root_node_id: string | null;
  position: Partial<Position>;
  stale: boolean;
  stale_since_revision: number | null;
  version: number;
  position_version: number;
  context_token_count: number | null;
  context_representation_version: number;
  context_content_hash: string | null;
  created_at: string;
  semantic_updated_at: string;
  position_updated_at: string;
  updated_at: string;
};

export type GraphEdge = {
  id: string;
  canvas_id: string;
  source_node_id: string;
  target_node_id: string;
  kind: EdgeKind;
  metadata: Record<string, unknown>;
  version: number;
  created_at: string;
  updated_at: string;
};

export type GraphCanvas = {
  id: string;
  title: string;
  revision: number;
  created_at: string;
  updated_at: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
};

const COLUMN_BY_KIND: Record<NodeKind, number> = {
  goal: 0,
  constraint: 0,
  strategy: 1,
  source: 1,
  claim: 2,
  generation_placeholder: 2,
  opportunity: 3,
  assumption: 4,
  risk: 4,
  validation_experiment: 4,
};

export function hasPosition(position: Partial<Position>): position is Position {
  return (
    typeof position.x === "number" &&
    Number.isFinite(position.x) &&
    typeof position.y === "number" &&
    Number.isFinite(position.y)
  );
}

export function graphSurfaceSize(
  nodes: ReadonlyArray<{ position: Partial<Position> }>,
): GraphSurfaceSize {
  let width = MIN_GRAPH_SURFACE_SIZE.width;
  let height = MIN_GRAPH_SURFACE_SIZE.height;

  for (const node of nodes) {
    if (!hasPosition(node.position)) continue;
    width = Math.max(
      width,
      node.position.x + GRAPH_NODE_WIDTH + GRAPH_SURFACE_CONTENT_MARGIN,
    );
    height = Math.max(
      height,
      node.position.y + GRAPH_NODE_HEIGHT + GRAPH_SURFACE_CONTENT_MARGIN,
    );
  }

  return { width, height };
}

export function clampNodePosition(
  position: Position,
  surface: GraphSurfaceSize,
): Position {
  return {
    x: Math.max(
      GRAPH_SURFACE_PADDING,
      Math.min(
        surface.width - GRAPH_NODE_WIDTH - GRAPH_SURFACE_PADDING,
        position.x,
      ),
    ),
    y: Math.max(
      GRAPH_SURFACE_PADDING,
      Math.min(
        surface.height - GRAPH_NODE_HEIGHT - GRAPH_SURFACE_PADDING,
        position.y,
      ),
    ),
  };
}

export function nextNodePosition(index: number): Position {
  return {
    x: 72 + (index % 4) * 286,
    y: 72 + Math.floor(index / 4) * 176,
  };
}

export function visibleNodePosition(node: GraphNode, index: number): Position {
  return hasPosition(node.position) ? node.position : nextNodePosition(index);
}

export function deterministicLayout(nodes: GraphNode[]): Map<string, Position> {
  const grouped = new Map<number, GraphNode[]>();

  for (const node of nodes) {
    const column = COLUMN_BY_KIND[node.kind];
    grouped.set(column, [...(grouped.get(column) ?? []), node]);
  }

  const positions = new Map<string, Position>();
  for (const [column, columnNodes] of grouped) {
    columnNodes
      .sort((left, right) =>
        `${left.title}\u0000${left.id}`.localeCompare(
          `${right.title}\u0000${right.id}`,
        ),
      )
      .forEach((node, row) => {
        positions.set(node.id, {
          x: 72 + column * 286,
          y: 72 + row * 176,
        });
      });
  }

  return positions;
}

export function isBranchRoot(node: GraphNode): boolean {
  return ["strategy", "claim", "opportunity"].includes(node.kind);
}
