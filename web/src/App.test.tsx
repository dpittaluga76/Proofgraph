import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { GraphCanvas, GraphEdge, GraphNode, NodeKind } from "./graph";

const CANVAS_ID = "11111111-1111-4111-8111-111111111111";

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
  vi.unstubAllGlobals();
});

describe("App", () => {
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

  it("reports an unavailable runtime without crashing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<App />);
    expect(await screen.findByText("Workspace unavailable")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Create canvas" }),
    ).toBeDisabled();
  });
});
