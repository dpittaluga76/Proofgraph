import { describe, expect, it } from "vitest";

import {
  clampNodePosition,
  deterministicLayout,
  graphSurfaceSize,
  type GraphNode,
} from "./graph";

function graphNode(
  id: string,
  kind: GraphNode["kind"],
  title: string,
): GraphNode {
  const timestamp = "2026-07-14T12:00:00+00:00";
  return {
    id,
    canvas_id: "canvas",
    kind,
    title,
    body: null,
    metadata: {},
    branch_root_node_id: null,
    position: {},
    stale: false,
    stale_since_revision: null,
    version: 1,
    position_version: 1,
    context_token_count: null,
    context_representation_version: 1,
    context_content_hash: null,
    created_at: timestamp,
    semantic_updated_at: timestamp,
    position_updated_at: timestamp,
    updated_at: timestamp,
  };
}

describe("deterministicLayout", () => {
  it("places the fixed ontology in readable columns independent of input order", () => {
    const goal = graphNode("goal", "goal", "Goal");
    const strategy = graphNode("strategy", "strategy", "Strategy");
    const claimB = graphNode("claim-b", "claim", "Beta claim");
    const claimA = graphNode("claim-a", "claim", "Alpha claim");
    const opportunity = graphNode("opportunity", "opportunity", "Opportunity");

    const first = deterministicLayout([
      opportunity,
      claimB,
      goal,
      claimA,
      strategy,
    ]);
    const second = deterministicLayout([
      claimA,
      strategy,
      opportunity,
      goal,
      claimB,
    ]);

    expect(Object.fromEntries(first)).toEqual(Object.fromEntries(second));
    expect(first.get(goal.id)).toEqual({ x: 72, y: 72 });
    expect(first.get(strategy.id)).toEqual({ x: 358, y: 72 });
    expect(first.get(claimA.id)).toEqual({ x: 644, y: 72 });
    expect(first.get(claimB.id)).toEqual({ x: 644, y: 248 });
    expect(first.get(opportunity.id)).toEqual({ x: 930, y: 72 });
  });

  it("expands the surface around the canonical goal and constraints", () => {
    const canonicalNodes = [
      graphNode("goal", "goal", "Primary goal"),
      ...Array.from({ length: 7 }, (_, index) =>
        graphNode(`constraint-${index}`, "constraint", `Constraint ${index}`),
      ),
    ];
    const positions = deterministicLayout(canonicalNodes);
    const positioned = canonicalNodes.map((node) => ({
      ...node,
      position: positions.get(node.id) ?? {},
    }));
    const surface = graphSurfaceSize(positioned);
    const lowestPosition = [...positions.values()].reduce((lowest, position) =>
      position.y > lowest.y ? position : lowest,
    );

    expect(lowestPosition).toEqual({ x: 72, y: 1304 });
    expect(surface.height).toBe(1468);
    expect(clampNodePosition(lowestPosition, surface)).toEqual(lowestPosition);
  });
});
