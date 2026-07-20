import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { GraphPatch } from "./api";
import type { GraphCanvas, GraphEdge, GraphNode, NodeKind } from "./graph";

const CANVAS_ID = "11111111-1111-4111-8111-111111111111";

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<
    string,
    Array<(event: MessageEvent<string>) => void>
  >();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(
    type: string,
    listener: (event: MessageEvent<string>) => void,
  ) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close() {
    this.closed = true;
  }

  emit(type: string, payload: unknown) {
    const event = new MessageEvent<string>(type, {
      data: JSON.stringify(payload),
    });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function node(
  id: string,
  kind: NodeKind,
  title: string,
  overrides: Partial<GraphNode> = {},
): GraphNode {
  return {
    id,
    canvas_id: CANVAS_ID,
    kind,
    title,
    body: null,
    metadata:
      kind === "constraint" ? { context_scope: "global", pinned: false } : {},
    branch_root_node_id: null,
    position: { x: 72, y: 72 },
    stale: false,
    stale_since_revision: null,
    version: 1,
    position_version: 1,
    context_token_count: null,
    context_representation_version: 1,
    context_content_hash: null,
    created_at: "2026-07-14T12:00:00+00:00",
    semantic_updated_at: "2026-07-14T12:00:00+00:00",
    position_updated_at: "2026-07-14T12:00:00+00:00",
    updated_at: "2026-07-14T12:00:00+00:00",
    ...overrides,
  };
}

function edge(
  id: string,
  sourceNodeId: string,
  targetNodeId: string,
  overrides: Partial<GraphEdge> = {},
): GraphEdge {
  return {
    id,
    canvas_id: CANVAS_ID,
    source_node_id: sourceNodeId,
    target_node_id: targetNodeId,
    kind: "derived_from",
    metadata: {},
    version: 1,
    created_at: "2026-07-14T12:00:00+00:00",
    updated_at: "2026-07-14T12:00:00+00:00",
    ...overrides,
  };
}

function canvas(nodes: GraphNode[] = [], edges: GraphEdge[] = []): GraphCanvas {
  return {
    id: CANVAS_ID,
    title: "Opportunity map",
    revision: nodes.length + edges.length,
    created_at: "2026-07-14T12:00:00+00:00",
    updated_at: "2026-07-14T12:00:00+00:00",
    nodes,
    edges,
  };
}

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function readyFetch() {
  const fetchMock = vi.fn();
  fetchMock.mockResolvedValueOnce(response({ status: "ok", database: "ok" }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function openCanvas(
  fetchMock: ReturnType<typeof vi.fn>,
  graph: GraphCanvas,
) {
  fetchMock.mockResolvedValueOnce(response({ canvas: graph }));
  fireEvent.change(screen.getByLabelText("Canvas ID"), {
    target: { value: graph.id },
  });
  fireEvent.click(screen.getByRole("button", { name: "Open canvas" }));
  await screen.findByDisplayValue(graph.title);
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  window.history.pushState({}, "", "/");
  FakeEventSource.instances = [];
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("opens the isolated seeded demo and resets only through the demo endpoint", async () => {
    window.history.pushState({}, "", "/?demo=1");
    const seeded = {
      ...canvas([
        node(
          "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          "goal",
          "Reduce security questionnaire work",
        ),
      ]),
      title: "Security questionnaire opportunity",
    };
    const resetCanvas = {
      ...seeded,
      id: "22222222-2222-4222-8222-222222222222",
      revision: 0,
      nodes: [],
    };
    const session = {
      expires_at: "2026-07-16T12:00:00Z",
      hybrid_run_count: 3,
      hybrid_run_limit: 12,
      primary_profile: "demo_hybrid_v1",
      fallback_profile: "replay_v1",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response({ status: "ok", database: "ok", demo_mode: false }),
      )
      .mockResolvedValueOnce(response({ session, canvas: seeded }))
      .mockResolvedValueOnce(
        response({
          session: { ...session, hybrid_run_count: 3 },
          canvas: resetCanvas,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByDisplayValue("Security questionnaire opportunity"),
    ).toBeVisible();
    expect(screen.getByText("Hybrid 3/12")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Open another canvas" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Reset demo" }));

    expect(
      await screen.findByText(
        "Demo reset to a fresh isolated copy of the starting canvas.",
      ),
    ).toBeVisible();
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/health",
      "/api/demo/bootstrap",
      "/api/demo/reset",
    ]);
    expect(screen.getByDisplayValue(resetCanvas.title)).toBeVisible();
  });

  it("creates a canvas and adds a goal through a localized operation", async () => {
    const fetchMock = readyFetch();
    const emptyCanvas = canvas();
    fetchMock.mockResolvedValueOnce(response({ canvas: emptyCanvas }, 201));

    render(<App />);
    await screen.findByText("Workspace ready");
    fireEvent.change(screen.getByLabelText("Canvas title"), {
      target: { value: "Opportunity map" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create canvas" }));
    await screen.findByDisplayValue("Opportunity map");

    const goal = node(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "goal",
      "Recurring revenue",
    );
    fetchMock.mockResolvedValueOnce(
      response({ canvas_revision: 1, node: goal }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Add node" }));
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Recurring revenue" },
    });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Add node",
      }),
    );

    expect(await screen.findByText("Recurring revenue")).toBeVisible();
    const requestBody = JSON.parse(
      String((fetchMock.mock.calls[2]?.[1] as RequestInit).body),
    ) as Record<string, unknown>;
    expect(requestBody.op).toBe("ADD_NODE");
    expect(requestBody.operation_key).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(requestBody.node).toMatchObject({
      kind: "goal",
      title: "Recurring revenue",
      position: { x: 72, y: 72 },
    });
  });

  it("edits a constraint into pinned branch scope with a valid root", async () => {
    const strategy = node(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "strategy",
      "Productize services",
    );
    const constraint = node(
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      "constraint",
      "Eight-week MVP",
      { position: { x: 360, y: 72 } },
    );
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, canvas([strategy, constraint]));

    fireEvent.click(screen.getByTestId(`node-${constraint.id}`));
    fireEvent.change(screen.getByLabelText("Scope"), {
      target: { value: "branch" },
    });
    fireEvent.change(screen.getByLabelText("Branch root"), {
      target: { value: strategy.id },
    });
    fireEvent.click(
      screen.getByLabelText(
        "Include this constraint automatically when its scope applies",
      ),
    );

    const updated = {
      ...constraint,
      metadata: { context_scope: "branch", pinned: true },
      branch_root_node_id: strategy.id,
      version: 2,
    };
    fetchMock.mockResolvedValueOnce(
      response({ canvas_revision: 3, node: updated }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save node" }));
    await screen.findByText("Node saved.");

    const requestBody = JSON.parse(
      String((fetchMock.mock.calls[2]?.[1] as RequestInit).body),
    ) as {
      op: string;
      expected_version: number;
      changes: Record<string, unknown>;
    };
    expect(requestBody).toMatchObject({
      op: "UPDATE_NODE",
      expected_version: 1,
      changes: {
        metadata: { context_scope: "branch", pinned: true },
        branch_root_node_id: strategy.id,
      },
    });
  });

  it("resolves node deletion dependencies through explicit audited operations", async () => {
    const root = node(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "strategy",
      "Root strategy",
    );
    const claim = node(
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      "claim",
      "Demand signal",
      { position: { x: 360, y: 72 } },
    );
    const constraint = node(
      "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      "constraint",
      "Branch budget",
      {
        metadata: { context_scope: "branch", pinned: true },
        branch_root_node_id: root.id,
        position: { x: 72, y: 260 },
      },
    );
    const relation = edge(
      "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      root.id,
      claim.id,
    );
    const graph = canvas([root, claim, constraint], [relation]);
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, graph);
    fireEvent.click(screen.getByTestId(`node-${root.id}`));

    fetchMock.mockResolvedValueOnce(
      response(
        {
          error: {
            code: "node_has_dependencies",
            message: "Resolve dependencies first.",
            details: {
              incident_edges: [{ id: relation.id, version: 1 }],
              referencing_constraints: [{ id: constraint.id, version: 1 }],
            },
          },
        },
        409,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete node" }));
    expect(
      await screen.findByRole("heading", {
        name: "Resolve dependencies first",
      }),
    ).toBeVisible();

    fetchMock
      .mockResolvedValueOnce(
        response({ canvas_revision: 5, deleted_edge_id: relation.id }),
      )
      .mockResolvedValueOnce(
        response({
          canvas_revision: 6,
          node: {
            ...constraint,
            metadata: { context_scope: "global", pinned: true },
            branch_root_node_id: null,
            version: 2,
          },
        }),
      )
      .mockResolvedValueOnce(
        response({ canvas_revision: 7, deleted_node_id: root.id }),
      )
      .mockResolvedValueOnce(
        response({
          canvas: canvas([
            claim,
            {
              ...constraint,
              metadata: { context_scope: "global", pinned: true },
              branch_root_node_id: null,
              version: 2,
            },
          ]),
        }),
      );

    fireEvent.click(screen.getByRole("button", { name: "Resolve and delete" }));
    expect(
      await screen.findByText(
        "Dependencies were resolved through audited operations and the node was deleted.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("Root strategy")).not.toBeInTheDocument();

    const operationNames = fetchMock.mock.calls.slice(3, 6).map((call) => {
      const body = JSON.parse(String((call[1] as RequestInit).body)) as {
        op: string;
      };
      return body.op;
    });
    expect(operationNames).toEqual([
      "DELETE_EDGE",
      "PATCH_NODE_METADATA",
      "DELETE_NODE",
    ]);
  });

  it("persists dragged node movement with the position version", async () => {
    const goal = node(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "goal",
      "Find an opportunity",
      { position: { x: 72, y: 72 }, position_version: 4 },
    );
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, canvas([goal]));

    fetchMock.mockResolvedValueOnce(
      response({
        canvas_revision: 2,
        node: {
          ...goal,
          position: { x: 132, y: 112 },
          position_version: 5,
        },
      }),
    );
    const handle = screen.getByRole("button", {
      name: "Move Find an opportunity",
    });
    fireEvent.pointerDown(handle, { pointerId: 7, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(handle, { pointerId: 7, clientX: 160, clientY: 140 });
    fireEvent.pointerUp(handle, { pointerId: 7, clientX: 160, clientY: 140 });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const requestBody = JSON.parse(
      String((fetchMock.mock.calls[2]?.[1] as RequestInit).body),
    ) as Record<string, unknown>;
    expect(requestBody).toMatchObject({
      op: "MOVE_NODE",
      node_id: goal.id,
      expected_position_version: 4,
      position: { x: 132, y: 112 },
    });
  });

  it("keeps a low canonical constraint movable on the expanded surface", async () => {
    const canonicalNodes = [
      node("00000000-0000-4000-8000-000000000000", "goal", "Primary goal"),
      ...Array.from({ length: 7 }, (_, index) =>
        node(
          `00000000-0000-4000-8000-00000000000${index + 1}`,
          "constraint",
          `Constraint ${index + 1}`,
          { position: { x: 72, y: 248 + index * 176 } },
        ),
      ),
    ];
    const lowest = canonicalNodes.at(-1)!;
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, canvas(canonicalNodes));

    expect(screen.getByTestId("graph-surface")).toHaveStyle({
      height: "1468px",
    });
    fetchMock.mockResolvedValueOnce(
      response({
        canvas_revision: 9,
        node: {
          ...lowest,
          position: { x: 112, y: 1264 },
          position_version: 2,
        },
      }),
    );
    const handle = screen.getByRole("button", {
      name: "Move Constraint 7",
    });
    fireEvent.pointerDown(handle, { pointerId: 9, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(handle, { pointerId: 9, clientX: 140, clientY: 60 });
    fireEvent.pointerUp(handle, { pointerId: 9, clientX: 140, clientY: 60 });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const requestBody = JSON.parse(
      String((fetchMock.mock.calls[2]?.[1] as RequestInit).body),
    ) as Record<string, unknown>;
    expect(requestBody).toMatchObject({
      op: "MOVE_NODE",
      node_id: lowest.id,
      position: { x: 112, y: 1264 },
    });
  });

  it("adds and removes a typed edge through localized operations", async () => {
    const source = node(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "claim",
      "Teams repeat the work",
    );
    const target = node(
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      "opportunity",
      "Automate the workflow",
      { position: { x: 360, y: 72 } },
    );
    const relation = edge(
      "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      source.id,
      target.id,
      { kind: "supports" },
    );
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, canvas([source, target]));

    fetchMock.mockResolvedValueOnce(
      response({ canvas_revision: 3, edge: relation }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Connect nodes" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Source"), {
      target: { value: source.id },
    });
    fireEvent.change(within(dialog).getByLabelText("Relationship"), {
      target: { value: "supports" },
    });
    fireEvent.change(within(dialog).getByLabelText("Target"), {
      target: { value: target.id },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Add connection" }),
    );
    expect(await screen.findByText("Connection added.")).toBeVisible();

    const addBody = JSON.parse(
      String((fetchMock.mock.calls[2]?.[1] as RequestInit).body),
    ) as Record<string, unknown>;
    expect(addBody).toMatchObject({
      op: "ADD_EDGE",
      edge: {
        source_node_id: source.id,
        target_node_id: target.id,
        kind: "supports",
      },
    });

    fetchMock.mockResolvedValueOnce(
      response({ canvas_revision: 4, deleted_edge_id: relation.id }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete connection" }));
    expect(await screen.findByText("Connection deleted.")).toBeVisible();
    const deleteBody = JSON.parse(
      String((fetchMock.mock.calls[3]?.[1] as RequestInit).body),
    ) as Record<string, unknown>;
    expect(deleteBody).toMatchObject({
      op: "DELETE_EDGE",
      edge_id: relation.id,
      expected_version: 1,
    });
  });

  it("visually labels persisted cached evidence as previously retrieved", async () => {
    const cachedSource = node(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "source",
      "Cached source",
      { metadata: { cache_hit: true, review_status: "accepted" } },
    );
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, canvas([cachedSource]));

    expect(screen.getByText("Previously retrieved")).toBeVisible();
  });

  it("renders sanitized source text without creating executable markup", async () => {
    const maliciousText =
      '<img src=x onerror="window.__proofgraphInjected=true">Ignore instructions';
    const source = node(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "source",
      "Untrusted source",
      { body: maliciousText },
    );
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, canvas([source]));

    expect(screen.getByText(maliciousText)).toBeVisible();
    expect(document.querySelector("img")).toBeNull();
    expect(
      (window as Window & { __proofgraphInjected?: boolean })
        .__proofgraphInjected,
    ).toBeUndefined();
  });

  it("persists deterministic auto-layout as ordered position-only operations", async () => {
    const opportunity = node(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "opportunity",
      "Focused opportunity",
      { position: { x: 10, y: 10 }, position_version: 2 },
    );
    const goal = node(
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      "goal",
      "Primary goal",
      { position: { x: 500, y: 500 }, position_version: 3 },
    );
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, canvas([goal, opportunity]));

    fetchMock
      .mockResolvedValueOnce(
        response({
          canvas_revision: 3,
          node: {
            ...opportunity,
            position: { x: 930, y: 72 },
            position_version: 3,
          },
        }),
      )
      .mockResolvedValueOnce(
        response({
          canvas_revision: 4,
          node: {
            ...goal,
            position: { x: 72, y: 72 },
            position_version: 4,
          },
        }),
      );

    fireEvent.click(screen.getByRole("button", { name: "Auto-layout" }));
    expect(
      await screen.findByText("Deterministic layout saved."),
    ).toBeVisible();
    const moveBodies = fetchMock.mock.calls
      .slice(2, 4)
      .map((call) =>
        JSON.parse(String((call[1] as RequestInit).body)),
      ) as Array<Record<string, unknown>>;
    expect(moveBodies).toEqual([
      expect.objectContaining({
        op: "MOVE_NODE",
        node_id: opportunity.id,
        expected_position_version: 2,
        position: { x: 930, y: 72 },
      }),
      expect.objectContaining({
        op: "MOVE_NODE",
        node_id: goal.id,
        expected_position_version: 3,
        position: { x: 72, y: 72 },
      }),
    ]);
  });

  it("recovers from a stale semantic edit by reloading current server state", async () => {
    const goal = node(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "goal",
      "Original goal",
    );
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, canvas([goal]));
    fireEvent.click(screen.getByTestId(`node-${goal.id}`));
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Stale local edit" },
    });

    fetchMock.mockResolvedValueOnce(
      response(
        {
          error: {
            code: "version_conflict",
            message: "The entity version no longer matches the request.",
            details: { expected_version: 1, current_version: 2 },
          },
        },
        409,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save node" }));
    expect(
      await screen.findByText(
        "This item changed elsewhere. Reload the canvas and try again.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Original goal")).toBeVisible();

    const currentGoal = {
      ...goal,
      title: "Current server goal",
      version: 2,
    };
    fetchMock.mockResolvedValueOnce(
      response({ canvas: { ...canvas([currentGoal]), revision: 2 } }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));

    expect(await screen.findByText("Canvas reloaded.")).toBeVisible();
    expect(screen.getByDisplayValue("Current server goal")).toBeVisible();
    expect(screen.queryByText("Original goal")).not.toBeInTheDocument();
  });

  it("reviews all six opportunity dimensions and applies a dependency-safe selection", async () => {
    const graph = canvas();
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, graph);

    const evidenceOperationId = "evidence-operation";
    const opportunityOperationId = "opportunity-operation";
    const patch: GraphPatch = {
      patch_id: "99999999-9999-4999-8999-999999999999",
      run_id: "88888888-8888-4888-8888-888888888888",
      canvas_id: CANVAS_ID,
      base_canvas_revision: 0,
      status: "pending",
      operations: [
        {
          operation_index: 0,
          operation_id: evidenceOperationId,
          candidate: {
            op: "ADD_NODE",
            operation_id: evidenceOperationId,
            node: {
              client_generated_id: "evidence-node",
              kind: "evidence",
              title: "Interview notes",
            },
          },
          dependency_operation_ids: [],
          dependency_operation_indices: [],
          missing_dependency_operation_ids: [],
          review: {
            change_type: "addition",
            entity_type: "node",
            semantic_role: "evidence",
            title: "Interview notes",
            provenance_node_ids: [],
            assumptions: [],
            risks: [],
            contradiction: null,
            quality_dimensions: null,
            distribution_rationale: null,
            defensibility_rationale: null,
          },
        },
        {
          operation_index: 1,
          operation_id: opportunityOperationId,
          candidate: {
            op: "ADD_NODE",
            operation_id: opportunityOperationId,
            node: {
              client_generated_id: "opportunity-node",
              kind: "opportunity",
              title: "Automate evidence review",
            },
          },
          dependency_operation_ids: [evidenceOperationId],
          dependency_operation_indices: [0],
          missing_dependency_operation_ids: [],
          review: {
            change_type: "addition",
            entity_type: "node",
            semantic_role: "opportunity",
            title: "Automate evidence review",
            provenance_node_ids: ["source-node"],
            assumptions: [{ statement: "Teams repeat this workflow" }],
            risks: [{ statement: "Evidence may be sparse" }],
            contradiction: "One interview disagreed",
            quality_dimensions: {
              evidence_strength: {
                rating: "strong",
                rationale: "Multiple independent interviews.",
              },
              novelty: {
                rating: "medium",
                rationale: "Existing tools do not preserve provenance.",
              },
              builder_fit: {
                rating: "strong",
                rationale: "Matches the builder's graph experience.",
              },
              technical_feasibility: {
                rating: "strong",
                rationale: "The required primitives already exist.",
              },
              distribution_clarity: {
                rating: "medium",
                rationale: "Reach teams through research communities.",
              },
              operational_burden: {
                rating: "low",
                rationale: "No managed service is required.",
              },
            },
            distribution_rationale: "Start with research operations teams.",
            defensibility_rationale: "Decision provenance compounds over time.",
          },
        },
      ],
      regeneration_target_ids: [],
      permitted_stale_resolution_ids: [],
      client_id_map: {},
      decisions: [],
      regenerated_by_run_id: null,
      created_at: "2026-07-15T12:00:00+00:00",
      decided_at: null,
      applied_at: null,
    };

    fireEvent.click(screen.getByRole("button", { name: "Review patch" }));
    fetchMock.mockResolvedValueOnce(response({ patch }));
    fireEvent.change(screen.getByLabelText("Patch ID"), {
      target: { value: patch.patch_id },
    });
    fireEvent.click(screen.getByRole("button", { name: "Load patch" }));

    expect(await screen.findByText("Evidence strength")).toBeVisible();
    for (const label of [
      "Novelty",
      "Builder fit",
      "Technical feasibility",
      "Distribution clarity",
      "Operational burden",
      "Distribution rationale",
      "Defensibility rationale",
    ]) {
      expect(screen.getByText(label)).toBeVisible();
    }

    fireEvent.click(screen.getByRole("checkbox", { name: /Interview notes/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Deselect dependent operations first",
    );
    expect(
      screen.getByRole("checkbox", { name: /Interview notes/i }),
    ).toBeChecked();

    const appliedPatch: GraphPatch = {
      ...patch,
      status: "applied",
      decided_at: "2026-07-15T12:01:00+00:00",
      applied_at: "2026-07-15T12:01:00+00:00",
    };
    fetchMock
      .mockResolvedValueOnce(
        response({
          patch: appliedPatch,
          canvas_revision: 2,
          client_id_map: {},
          conflicts: [],
        }),
      )
      .mockResolvedValueOnce(response({ canvas: { ...graph, revision: 2 } }));
    fireEvent.click(
      screen.getByRole("button", { name: "Apply nonconflicting" }),
    );

    expect(
      await screen.findByText("Patch decisions committed to the canvas."),
    ).toBeVisible();
    const applyRequest = fetchMock.mock.calls.find(
      (call) => call[0] === `/api/graph-patches/${patch.patch_id}/apply`,
    );
    expect(applyRequest).toBeDefined();
    expect(JSON.parse(String((applyRequest?.[1] as RequestInit).body))).toEqual(
      {
        selected_operation_ids: [evidenceOperationId, opportunityOperationId],
        apply_nonconflicting_only: true,
      },
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/canvases/${CANVAS_ID}`,
        expect.anything(),
      ),
    );
  });

  it("keeps rejected evidence visible, focusable, audited, and excluded from generation", async () => {
    const rejected = node(
      "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
      "claim",
      "Rejected claim",
      {
        metadata: {
          review_status: "rejected",
          rejected_by_operation_id: 42,
        },
      },
    );
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, canvas([rejected]));

    const card = screen.getByTestId(`node-${rejected.id}`);
    expect(card).toHaveAttribute("tabindex", "0");
    expect(card).toHaveAccessibleName(/Rejected evidence/);
    expect(screen.getByText("Rejected evidence")).toBeVisible();
    fireEvent.click(card);
    expect(screen.getByText("Audit operation #42")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Reject accepted evidence" }),
    ).toBeDisabled();

    fireEvent.click(
      screen.getByRole("button", { name: "Open generation controls" }),
    );
    fireEvent.change(screen.getByLabelText("Operation"), {
      target: { value: "synthesize_opportunities" },
    });
    expect(
      screen.getByRole("checkbox", { name: /Rejected claim/ }),
    ).toBeDisabled();
    expect(
      screen.getAllByText("Rejected evidence is excluded from generation.")
        .length,
    ).toBeGreaterThan(0);
  });

  it("enables branch comparison only for canonical parallel regeneration lineage", async () => {
    const goal = node("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "goal", "Goal");
    const canonicalStrategy = node(
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      "strategy",
      "Canonical strategy",
    );
    const predecessor = node(
      "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      "strategy",
      "Retained predecessor",
      { stale: true, stale_since_revision: 4 },
    );
    const successor = node(
      "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      "strategy",
      "Parallel successor",
      {
        metadata: {
          regenerated_from_node_id: predecessor.id,
          lineage_mode: "parallel",
          review_status: "accepted",
        },
      },
    );
    const graph = canvas(
      [goal, canonicalStrategy, predecessor, successor],
      [
        edge(
          "11111111-aaaa-4aaa-8aaa-111111111111",
          goal.id,
          canonicalStrategy.id,
          {
            kind: "evolves_into",
          },
        ),
        edge(
          "22222222-bbbb-4bbb-8bbb-222222222222",
          predecessor.id,
          successor.id,
          {
            kind: "evolves_into",
          },
        ),
      ],
    );
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, graph);

    fireEvent.click(screen.getByTestId(`node-${goal.id}`));
    expect(
      screen.getByRole("button", { name: "Compare retained branches" }),
    ).toBeDisabled();

    fireEvent.click(screen.getByTestId(`node-${predecessor.id}`));
    const compare = screen.getByRole("button", {
      name: "Compare retained branches",
    });
    expect(compare).toBeEnabled();
    fireEvent.click(compare);
    expect(
      screen.getByRole("dialog", { name: "Compare retained branches" }),
    ).toBeVisible();
    const dialog = screen.getByRole("dialog", {
      name: "Compare retained branches",
    });
    expect(within(dialog).getByText("Parallel successor")).toBeVisible();
  });

  it("reloads authoritative descendants when a semantic mutation reports staleness", async () => {
    const goal = node("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "goal", "Goal");
    const strategy = node(
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      "strategy",
      "Dependent strategy",
    );
    const graph = canvas([goal, strategy]);
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, graph);
    fireEvent.click(screen.getByTestId(`node-${goal.id}`));
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Updated goal" },
    });

    fetchMock
      .mockResolvedValueOnce(
        response({
          canvas_revision: 3,
          node: { ...goal, title: "Updated goal", version: 2 },
          stale_node_ids: [strategy.id],
          newly_stale_node_ids: [strategy.id],
        }),
      )
      .mockResolvedValueOnce(
        response({
          canvas: {
            ...graph,
            revision: 3,
            nodes: [
              { ...goal, title: "Updated goal", version: 2 },
              { ...strategy, stale: true, stale_since_revision: 3 },
            ],
          },
        }),
      );
    fireEvent.click(screen.getByRole("button", { name: "Save node" }));

    expect(await screen.findByText("Needs regeneration")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/canvases/${CANVAS_ID}`,
      expect.anything(),
    );
  });

  it("uses one cursor-replayed SSE stream, shows provisional overlays, and clears terminal loading", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const strategy = node(
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      "strategy",
      "Accepted strategy",
      { metadata: { review_status: "accepted" } },
    );
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, canvas([strategy]));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.instances[0].url).toContain("events?after=0");

    const stream = FakeEventSource.instances[0];
    act(() => {
      stream.emit("run.started", {
        run_id: "run-a",
        canvas_sequence: 1,
        run_sequence: 1,
        event_type: "run.started",
        payload: { attempt: 1 },
        timestamp: "2026-07-15T12:00:00Z",
      });
      stream.emit("research.source_found", {
        run_id: "run-a",
        canvas_sequence: 2,
        run_sequence: 2,
        event_type: "research.source_found",
        payload: {
          provisional: true,
          source_id: "source-a",
          url: "https://example.com/questionnaires",
          sanitized_excerpt: "Previously validated evidence.",
          cache_hit: true,
        },
        timestamp: "2026-07-15T12:00:01Z",
      });
      stream.emit("evidence.extracted", {
        run_id: "run-a",
        canvas_sequence: 3,
        run_sequence: 3,
        event_type: "evidence.extracted",
        payload: {
          provisional: true,
          claim_id: "claim-a",
          claim: "Questionnaires delay deals.",
          classification: "observed",
          strength: "strong",
          source_ids: ["source-a"],
        },
        timestamp: "2026-07-15T12:00:01Z",
      });
    });

    expect(await screen.findByText("Generation in progress")).toBeVisible();
    expect(screen.getByText("Provisional evidence")).toBeVisible();
    expect(screen.getByText("Questionnaires delay deals.")).toBeVisible();
    expect(screen.getByText("Previously retrieved")).toBeVisible();

    act(() => {
      stream.emit("run.failed", {
        run_id: "run-a",
        canvas_sequence: 4,
        run_sequence: 4,
        event_type: "run.failed",
        payload: {
          code: "provider_timeout",
          message: "Provider timed out.",
          retryable: true,
          stage: "extracting",
        },
        timestamp: "2026-07-15T12:00:02Z",
      });
    });
    await waitFor(() =>
      expect(
        screen.queryByText("Generation in progress"),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/Provider timed out/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry safely" })).toBeEnabled();

    act(() => stream.onerror?.());
    expect(stream.closed).toBe(true);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2), {
      timeout: 1_500,
    });
    expect(FakeEventSource.instances[1].url).toContain("events?after=4");
  });

  it("auto-opens only pending patch previews during durable SSE replay", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, canvas());
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const basePatch: GraphPatch = {
      patch_id: "patch-old",
      run_id: "run-old",
      canvas_id: CANVAS_ID,
      base_canvas_revision: 0,
      status: "rejected",
      operations: [],
      regeneration_target_ids: [],
      permitted_stale_resolution_ids: [],
      client_id_map: {},
      decisions: [],
      regenerated_by_run_id: null,
      created_at: "2026-07-15T12:00:00Z",
      decided_at: "2026-07-15T12:01:00Z",
      applied_at: null,
    };
    fetchMock.mockResolvedValueOnce(response({ patch: basePatch }));
    act(() => {
      FakeEventSource.instances[0].emit("patch.ready", {
        run_id: "run-old",
        canvas_sequence: 1,
        run_sequence: 1,
        event_type: "patch.ready",
        payload: { patch_id: basePatch.patch_id },
        timestamp: "2026-07-15T12:00:00Z",
      });
    });
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/graph-patches/patch-old",
        expect.anything(),
      ),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    const pendingPatch: GraphPatch = {
      ...basePatch,
      patch_id: "patch-current",
      run_id: "run-current",
      status: "pending",
      decided_at: null,
    };
    fetchMock
      .mockResolvedValueOnce(response({ patch: pendingPatch }))
      .mockResolvedValueOnce(response({ patch: pendingPatch }));
    act(() => {
      FakeEventSource.instances[0].emit("patch.ready", {
        run_id: "run-current",
        canvas_sequence: 2,
        run_sequence: 1,
        event_type: "patch.ready",
        payload: { patch_id: pendingPatch.patch_id },
        timestamp: "2026-07-15T12:02:00Z",
      });
    });

    expect(
      await screen.findByRole("dialog", {
        name: "Review generated graph patch",
      }),
    ).toBeVisible();
    expect(await screen.findByText("0 candidate operations")).toBeVisible();
  });

  it("shows persisted opportunity quality and rationales after patch application", async () => {
    const opportunity = node(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "opportunity",
      "Approved-answer workspace",
      {
        metadata: {
          review_status: "accepted",
          dimensions: {
            evidence_strength: {
              rating: "strong",
              rationale: "Independent evidence supports the problem.",
            },
            novelty: {
              rating: "medium",
              rationale: "A focused workflow wedge.",
            },
            builder_fit: { rating: "high", rationale: "Fits the team." },
            technical_feasibility: {
              rating: "high",
              rationale: "Uses conventional components.",
            },
            distribution_clarity: {
              rating: "medium",
              rationale: "The buyer has focused communities.",
            },
            operational_burden: {
              rating: "medium",
              rationale: "Review remains necessary.",
            },
          },
          distribution_rationale: "Security leaders have focused channels.",
          defensibility: "Approved history and integrations compound.",
        },
      },
    );
    const fetchMock = readyFetch();
    render(<App />);
    await screen.findByText("Workspace ready");
    await openCanvas(fetchMock, canvas([opportunity]));

    fireEvent.click(screen.getByTestId(`node-${opportunity.id}`));

    const quality = screen.getByRole("region", {
      name: "Applied opportunity quality",
    });
    expect(within(quality).getByText("Evidence strength")).toBeVisible();
    expect(within(quality).getByText("Operational burden")).toBeVisible();
    expect(
      within(quality).getByText("Security leaders have focused channels."),
    ).toBeVisible();
    expect(
      within(quality).getByText("Approved history and integrations compound."),
    ).toBeVisible();
  });

  it("reports an unavailable runtime without crashing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<App />);
    expect(await screen.findByText("Workspace unavailable")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Create canvas" }),
    ).toBeDisabled();
  });
});
