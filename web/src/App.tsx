import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  ApiError,
  applyOperation,
  checkRuntime,
  createCanvas,
  type DeleteEdgeResult,
  type DeleteNodeResult,
  type DependencyConflictDetails,
  type EdgeOperationResult,
  getCanvas,
  type NodeOperationResult,
  operationKey,
  renameCanvas,
} from "./api";
import {
  CREATABLE_NODE_KINDS,
  clampNodePosition,
  deterministicLayout,
  EDGE_KIND_LABELS,
  EDGE_KINDS,
  type EdgeKind,
  type GraphCanvas,
  type GraphEdge,
  type GraphNode,
  graphSurfaceSize,
  isBranchRoot,
  nextNodePosition,
  NODE_KIND_LABELS,
  type NodeKind,
  type Position,
  visibleNodePosition,
} from "./graph";

type RuntimeState = "checking" | "ready" | "unavailable";
type Selection = { type: "node" | "edge"; id: string } | null;
type Notice = { tone: "success" | "error"; message: string } | null;
type Dialog = "node" | "edge" | null;

type DeleteConflict = {
  node: GraphNode;
  details: DependencyConflictDetails;
};

type DragState = {
  pointerId: number;
  nodeId: string;
  startX: number;
  startY: number;
  origin: Position;
  expectedPositionVersion: number;
};

type NewNodeInput = {
  kind: NodeKind;
  title: string;
  body: string;
  metadata: Record<string, unknown>;
  branchRootNodeId: string | null;
};

type NodeEditInput = {
  title: string;
  body: string;
  contextScope?: "global" | "branch";
  pinned?: boolean;
  branchRootNodeId?: string | null;
};

type EdgeInput = {
  sourceNodeId: string;
  targetNodeId: string;
  kind: EdgeKind;
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "version_conflict") {
      return "This item changed elsewhere. Reload the canvas and try again.";
    }
    return error.message;
  }
  return error instanceof Error
    ? error.message
    : "The request could not be completed.";
}

function isDependencyConflict(
  value: unknown,
): value is DependencyConflictDetails {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const details = value as Partial<DependencyConflictDetails>;
  return (
    Array.isArray(details.incident_edges) &&
    Array.isArray(details.referencing_constraints)
  );
}

function upsertNode(
  canvas: GraphCanvas,
  result: NodeOperationResult,
): GraphCanvas {
  const exists = canvas.nodes.some((node) => node.id === result.node.id);
  return {
    ...canvas,
    revision: result.canvas_revision,
    nodes: exists
      ? canvas.nodes.map((node) =>
          node.id === result.node.id ? result.node : node,
        )
      : [...canvas.nodes, result.node],
  };
}

function upsertEdge(
  canvas: GraphCanvas,
  result: EdgeOperationResult,
): GraphCanvas {
  const exists = canvas.edges.some((edge) => edge.id === result.edge.id);
  return {
    ...canvas,
    revision: result.canvas_revision,
    edges: exists
      ? canvas.edges.map((edge) =>
          edge.id === result.edge.id ? result.edge : edge,
        )
      : [...canvas.edges, result.edge],
  };
}

export function App() {
  const [runtimeState, setRuntimeState] = useState<RuntimeState>("checking");
  const [canvas, setCanvas] = useState<GraphCanvas | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [deleteConflict, setDeleteConflict] = useState<DeleteConflict | null>(
    null,
  );
  const [recentCanvasId, setRecentCanvasId] = useState<string | null>(() => {
    try {
      return localStorage.getItem("proofgraph.recentCanvasId");
    } catch {
      return null;
    }
  });
  const dragState = useRef<DragState | null>(null);

  useEffect(() => {
    let active = true;
    void checkRuntime()
      .then(() => {
        if (active) setRuntimeState("ready");
      })
      .catch(() => {
        if (active) setRuntimeState("unavailable");
      });
    return () => {
      active = false;
    };
  }, []);

  const viewNodes = useMemo(
    () =>
      (canvas?.nodes ?? []).map((node, index) => ({
        ...node,
        position: visibleNodePosition(node, index),
      })),
    [canvas?.nodes],
  );
  const surfaceSize = useMemo(() => graphSurfaceSize(viewNodes), [viewNodes]);

  const selectedNode =
    selection?.type === "node"
      ? (canvas?.nodes.find((node) => node.id === selection.id) ?? null)
      : null;
  const selectedEdge =
    selection?.type === "edge"
      ? (canvas?.edges.find((edge) => edge.id === selection.id) ?? null)
      : null;

  function rememberCanvas(canvasId: string) {
    setRecentCanvasId(canvasId);
    try {
      localStorage.setItem("proofgraph.recentCanvasId", canvasId);
    } catch {
      // The workspace still works when storage is unavailable.
    }
  }

  async function loadCanvas(canvasId: string) {
    setBusy("open");
    setNotice(null);
    try {
      const loaded = await getCanvas(canvasId.trim());
      setCanvas(loaded);
      setSelection(null);
      rememberCanvas(loaded.id);
    } catch (error) {
      setNotice({ tone: "error", message: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function handleCreateCanvas(title: string) {
    setBusy("create-canvas");
    setNotice(null);
    try {
      const created = await createCanvas(title);
      setCanvas(created);
      setSelection(null);
      rememberCanvas(created.id);
      setNotice({ tone: "success", message: "Canvas created." });
    } catch (error) {
      setNotice({ tone: "error", message: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function refreshCurrentCanvas() {
    if (!canvas) return;
    setBusy("reload");
    try {
      const refreshed = await getCanvas(canvas.id);
      setCanvas(refreshed);
      setSelection((current) => {
        if (!current) return null;
        const collection =
          current.type === "node" ? refreshed.nodes : refreshed.edges;
        return collection.some((item) => item.id === current.id)
          ? current
          : null;
      });
      setNotice({ tone: "success", message: "Canvas reloaded." });
    } catch (error) {
      setNotice({ tone: "error", message: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function handleRename(title: string) {
    if (!canvas) return;
    setBusy("rename");
    try {
      const renamed = await renameCanvas(canvas.id, title);
      setCanvas((current) =>
        current
          ? { ...current, title: renamed.title, updated_at: renamed.updated_at }
          : current,
      );
      setNotice({ tone: "success", message: "Canvas title saved." });
    } catch (error) {
      setNotice({ tone: "error", message: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function handleAddNode(input: NewNodeInput) {
    if (!canvas) return;
    setBusy("add-node");
    setNotice(null);
    try {
      const node: Record<string, unknown> = {
        kind: input.kind,
        title: input.title,
        body: input.body,
        metadata: input.metadata,
        position: nextNodePosition(canvas.nodes.length),
      };
      if (input.kind === "constraint") {
        node.branch_root_node_id = input.branchRootNodeId;
      }
      const result = await applyOperation<NodeOperationResult>(canvas.id, {
        op: "ADD_NODE",
        operation_key: operationKey(),
        node,
      });
      setCanvas((current) => (current ? upsertNode(current, result) : current));
      setSelection({ type: "node", id: result.node.id });
      setDialog(null);
      setNotice({
        tone: "success",
        message: `${NODE_KIND_LABELS[input.kind]} added.`,
      });
    } catch (error) {
      setNotice({ tone: "error", message: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function handleSaveNode(node: GraphNode, input: NodeEditInput) {
    if (!canvas) return;
    setBusy("save-node");
    setNotice(null);
    const changes: Record<string, unknown> = {
      title: input.title,
      body: input.body,
    };
    if (node.kind === "constraint") {
      changes.metadata = {
        context_scope: input.contextScope,
        pinned: input.pinned,
      };
      changes.branch_root_node_id = input.branchRootNodeId;
    }
    try {
      const result = await applyOperation<NodeOperationResult>(canvas.id, {
        op: "UPDATE_NODE",
        operation_key: operationKey(),
        node_id: node.id,
        expected_version: node.version,
        changes,
      });
      setCanvas((current) => (current ? upsertNode(current, result) : current));
      setNotice({ tone: "success", message: "Node saved." });
    } catch (error) {
      setNotice({ tone: "error", message: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function handleDeleteNode(node: GraphNode) {
    if (!canvas) return;
    setBusy("delete-node");
    setNotice(null);
    try {
      const result = await applyOperation<DeleteNodeResult>(canvas.id, {
        op: "DELETE_NODE",
        operation_key: operationKey(),
        node_id: node.id,
        expected_version: node.version,
      });
      setCanvas((current) =>
        current
          ? {
              ...current,
              revision: result.canvas_revision,
              nodes: current.nodes.filter(
                (item) => item.id !== result.deleted_node_id,
              ),
            }
          : current,
      );
      setSelection(null);
      setNotice({ tone: "success", message: "Node deleted." });
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.code === "node_has_dependencies" &&
        isDependencyConflict(error.details)
      ) {
        setDeleteConflict({ node, details: error.details });
      } else {
        setNotice({ tone: "error", message: errorMessage(error) });
      }
    } finally {
      setBusy(null);
    }
  }

  async function resolveDependenciesAndDelete() {
    if (!canvas || !deleteConflict) return;
    setBusy("resolve-delete");
    setNotice(null);
    try {
      for (const edge of deleteConflict.details.incident_edges) {
        await applyOperation<DeleteEdgeResult>(canvas.id, {
          op: "DELETE_EDGE",
          operation_key: operationKey(),
          edge_id: edge.id,
          expected_version: edge.version,
        });
      }
      for (const constraint of deleteConflict.details.referencing_constraints) {
        await applyOperation<NodeOperationResult>(canvas.id, {
          op: "PATCH_NODE_METADATA",
          operation_key: operationKey(),
          node_id: constraint.id,
          expected_version: constraint.version,
          metadata: { context_scope: "global" },
        });
      }
      await applyOperation<DeleteNodeResult>(canvas.id, {
        op: "DELETE_NODE",
        operation_key: operationKey(),
        node_id: deleteConflict.node.id,
        expected_version: deleteConflict.node.version,
      });
      const refreshed = await getCanvas(canvas.id);
      setCanvas(refreshed);
      setSelection(null);
      setDeleteConflict(null);
      setNotice({
        tone: "success",
        message:
          "Dependencies were resolved through audited operations and the node was deleted.",
      });
    } catch (error) {
      setNotice({ tone: "error", message: errorMessage(error) });
      try {
        setCanvas(await getCanvas(canvas.id));
      } catch {
        // Keep the primary operation error visible.
      }
    } finally {
      setBusy(null);
    }
  }

  async function handleAddEdge(input: EdgeInput) {
    if (!canvas) return;
    setBusy("add-edge");
    setNotice(null);
    try {
      const result = await applyOperation<EdgeOperationResult>(canvas.id, {
        op: "ADD_EDGE",
        operation_key: operationKey(),
        edge: {
          source_node_id: input.sourceNodeId,
          target_node_id: input.targetNodeId,
          kind: input.kind,
        },
      });
      setCanvas((current) => (current ? upsertEdge(current, result) : current));
      setSelection({ type: "edge", id: result.edge.id });
      setDialog(null);
      setNotice({ tone: "success", message: "Connection added." });
    } catch (error) {
      setNotice({ tone: "error", message: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function handleSaveEdge(edge: GraphEdge, input: EdgeInput) {
    if (!canvas) return;
    setBusy("save-edge");
    try {
      const result = await applyOperation<EdgeOperationResult>(canvas.id, {
        op: "UPDATE_EDGE",
        operation_key: operationKey(),
        edge_id: edge.id,
        expected_version: edge.version,
        changes: {
          source_node_id: input.sourceNodeId,
          target_node_id: input.targetNodeId,
          kind: input.kind,
        },
      });
      setCanvas((current) => (current ? upsertEdge(current, result) : current));
      setNotice({ tone: "success", message: "Connection saved." });
    } catch (error) {
      setNotice({ tone: "error", message: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function handleDeleteEdge(edge: GraphEdge) {
    if (!canvas) return;
    setBusy("delete-edge");
    try {
      const result = await applyOperation<DeleteEdgeResult>(canvas.id, {
        op: "DELETE_EDGE",
        operation_key: operationKey(),
        edge_id: edge.id,
        expected_version: edge.version,
      });
      setCanvas((current) =>
        current
          ? {
              ...current,
              revision: result.canvas_revision,
              edges: current.edges.filter(
                (item) => item.id !== result.deleted_edge_id,
              ),
            }
          : current,
      );
      setSelection(null);
      setNotice({ tone: "success", message: "Connection deleted." });
    } catch (error) {
      setNotice({ tone: "error", message: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function handleAutoLayout() {
    if (!canvas) return;
    setBusy("layout");
    setNotice(null);
    try {
      const positions = deterministicLayout(canvas.nodes);
      let revision = canvas.revision;
      const updatedNodes: GraphNode[] = [];
      for (const node of [...canvas.nodes].sort((left, right) =>
        left.id.localeCompare(right.id),
      )) {
        const position = positions.get(node.id);
        if (!position) continue;
        const result = await applyOperation<NodeOperationResult>(canvas.id, {
          op: "MOVE_NODE",
          operation_key: operationKey(),
          node_id: node.id,
          expected_position_version: node.position_version,
          position,
        });
        revision = result.canvas_revision;
        updatedNodes.push(result.node);
      }
      const byId = new Map(updatedNodes.map((node) => [node.id, node]));
      setCanvas((current) =>
        current
          ? {
              ...current,
              revision,
              nodes: current.nodes.map((node) => byId.get(node.id) ?? node),
            }
          : current,
      );
      setNotice({ tone: "success", message: "Deterministic layout saved." });
    } catch (error) {
      setNotice({ tone: "error", message: errorMessage(error) });
      try {
        setCanvas(await getCanvas(canvas.id));
      } catch {
        // Keep the layout error visible.
      }
    } finally {
      setBusy(null);
    }
  }

  function beginDrag(
    event: ReactPointerEvent<HTMLButtonElement>,
    node: GraphNode,
  ) {
    if (busy) return;
    const index = viewNodes.findIndex((item) => item.id === node.id);
    const origin = visibleNodePosition(node, Math.max(index, 0));
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragState.current = {
      pointerId: event.pointerId,
      nodeId: node.id,
      startX: event.clientX,
      startY: event.clientY,
      origin,
      expectedPositionVersion: node.position_version,
    };
    setSelection({ type: "node", id: node.id });
  }

  function moveDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const drag = dragState.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const position = clampNodePosition(
      {
        x: drag.origin.x + event.clientX - drag.startX,
        y: drag.origin.y + event.clientY - drag.startY,
      },
      surfaceSize,
    );
    setCanvas((current) =>
      current
        ? {
            ...current,
            nodes: current.nodes.map((node) =>
              node.id === drag.nodeId ? { ...node, position } : node,
            ),
          }
        : current,
    );
  }

  function cancelDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const drag = dragState.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragState.current = null;
    setCanvas((current) =>
      current
        ? {
            ...current,
            nodes: current.nodes.map((node) =>
              node.id === drag.nodeId
                ? { ...node, position: drag.origin }
                : node,
            ),
          }
        : current,
    );
  }

  async function finishDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const drag = dragState.current;
    if (!drag || drag.pointerId !== event.pointerId || !canvas) return;
    dragState.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    const deltaX = event.clientX - drag.startX;
    const deltaY = event.clientY - drag.startY;
    if (Math.abs(deltaX) < 3 && Math.abs(deltaY) < 3) return;
    const position = clampNodePosition(
      { x: drag.origin.x + deltaX, y: drag.origin.y + deltaY },
      surfaceSize,
    );
    try {
      const result = await applyOperation<NodeOperationResult>(canvas.id, {
        op: "MOVE_NODE",
        operation_key: operationKey(),
        node_id: drag.nodeId,
        expected_position_version: drag.expectedPositionVersion,
        position,
      });
      setCanvas((current) => (current ? upsertNode(current, result) : current));
    } catch (error) {
      setNotice({ tone: "error", message: errorMessage(error) });
      try {
        setCanvas(await getCanvas(canvas.id));
      } catch {
        // Keep the movement error visible.
      }
    }
  }

  if (!canvas) {
    return (
      <Welcome
        runtimeState={runtimeState}
        recentCanvasId={recentCanvasId}
        busy={busy}
        notice={notice}
        onCreate={handleCreateCanvas}
        onOpen={loadCanvas}
      />
    );
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <div className="brand-block">
          <span className="brand-mark" aria-hidden="true">
            P
          </span>
          <div>
            <p className="eyebrow">Evidence-native opportunity canvas</p>
            <strong>Proofgraph</strong>
          </div>
        </div>
        <form
          className="canvas-title-form"
          key={canvas.title}
          onSubmit={(event) => {
            event.preventDefault();
            const title = String(
              new FormData(event.currentTarget).get("title") ?? "",
            ).trim();
            if (title) void handleRename(title);
          }}
        >
          <label htmlFor="canvas-title">Canvas</label>
          <input id="canvas-title" name="title" defaultValue={canvas.title} />
          <button
            type="submit"
            className="quiet-button"
            disabled={busy !== null}
          >
            Save title
          </button>
        </form>
        <div className="canvas-meta" aria-label="Canvas status">
          <span>Revision {canvas.revision}</span>
          <span>{canvas.nodes.length} nodes</span>
          <span>{canvas.edges.length} edges</span>
        </div>
      </header>

      <section className="toolbar" aria-label="Canvas tools">
        <button
          type="button"
          className="primary-button"
          onClick={() => setDialog("node")}
        >
          Add node
        </button>
        <button
          type="button"
          className="toolbar-button"
          disabled={canvas.nodes.length < 2}
          onClick={() => setDialog("edge")}
        >
          Connect nodes
        </button>
        <button
          type="button"
          className="toolbar-button"
          disabled={canvas.nodes.length === 0 || busy !== null}
          onClick={() => void handleAutoLayout()}
        >
          Auto-layout
        </button>
        <button
          type="button"
          className="toolbar-button"
          disabled={busy !== null}
          onClick={() => void refreshCurrentCanvas()}
        >
          Reload
        </button>
        <button
          type="button"
          className="toolbar-button toolbar-button--end"
          onClick={() => {
            setCanvas(null);
            setSelection(null);
            setNotice(null);
          }}
        >
          Open another canvas
        </button>
      </section>

      {notice && (
        <div
          className={`notice notice--${notice.tone}`}
          role={notice.tone === "error" ? "alert" : "status"}
        >
          {notice.message}
        </div>
      )}

      <div className="workspace-grid">
        <section className="canvas-panel" aria-label="Graph canvas">
          <div className="canvas-scroll">
            <div
              className="graph-surface"
              data-testid="graph-surface"
              style={surfaceSize}
              onClick={(event) => {
                if (event.target === event.currentTarget) setSelection(null);
              }}
            >
              {canvas.edges.map((edge) => (
                <EdgeConnection
                  key={edge.id}
                  edge={edge}
                  nodes={viewNodes}
                  selected={
                    selection?.type === "edge" && selection.id === edge.id
                  }
                  onSelect={() => setSelection({ type: "edge", id: edge.id })}
                />
              ))}
              {viewNodes.map((node) => (
                <NodeCard
                  key={node.id}
                  node={node}
                  selected={
                    selection?.type === "node" && selection.id === node.id
                  }
                  onSelect={() => setSelection({ type: "node", id: node.id })}
                  onPointerDown={(event) => beginDrag(event, node)}
                  onPointerMove={moveDrag}
                  onPointerUp={(event) => void finishDrag(event)}
                  onPointerCancel={cancelDrag}
                />
              ))}
              {canvas.nodes.length === 0 && (
                <div className="empty-canvas">
                  <span className="empty-canvas__glyph" aria-hidden="true">
                    +
                  </span>
                  <h2>Start with what you know</h2>
                  <p>
                    Add a goal and the constraints that shape what you can
                    build.
                  </p>
                  <button
                    type="button"
                    className="primary-button"
                    onClick={() => setDialog("node")}
                  >
                    Add the first node
                  </button>
                </div>
              )}
            </div>
          </div>
        </section>

        <aside className="inspector" aria-label="Selection inspector">
          {selectedNode && (
            <NodeInspector
              key={`${selectedNode.id}:${selectedNode.version}`}
              node={selectedNode}
              nodes={canvas.nodes}
              disabled={busy !== null}
              onSave={(input) => void handleSaveNode(selectedNode, input)}
              onDelete={() => void handleDeleteNode(selectedNode)}
            />
          )}
          {selectedEdge && (
            <EdgeInspector
              key={`${selectedEdge.id}:${selectedEdge.version}`}
              edge={selectedEdge}
              nodes={canvas.nodes}
              disabled={busy !== null}
              onSave={(input) => void handleSaveEdge(selectedEdge, input)}
              onDelete={() => void handleDeleteEdge(selectedEdge)}
            />
          )}
          {!selectedNode && !selectedEdge && (
            <div className="inspector-empty">
              <p className="inspector-kicker">Inspector</p>
              <h2>Select an argument</h2>
              <p>
                Choose a node or connection to edit it. Drag a node by its
                header to move it.
              </p>
              <div className="legend" aria-label="Graph legend">
                {(
                  [
                    "goal",
                    "constraint",
                    "strategy",
                    "claim",
                    "opportunity",
                    "risk",
                  ] as NodeKind[]
                ).map((kind) => (
                  <span
                    key={kind}
                    className={`legend-chip legend-chip--${kind}`}
                  >
                    {NODE_KIND_LABELS[kind]}
                  </span>
                ))}
              </div>
            </div>
          )}
        </aside>
      </div>

      {dialog === "node" && (
        <NodeDialog
          nodes={canvas.nodes}
          disabled={busy !== null}
          onClose={() => setDialog(null)}
          onSubmit={(input) => void handleAddNode(input)}
        />
      )}
      {dialog === "edge" && (
        <EdgeDialog
          nodes={canvas.nodes}
          disabled={busy !== null}
          onClose={() => setDialog(null)}
          onSubmit={(input) => void handleAddEdge(input)}
        />
      )}
      {deleteConflict && (
        <DeleteConflictDialog
          conflict={deleteConflict}
          disabled={busy !== null}
          onCancel={() => setDeleteConflict(null)}
          onResolve={() => void resolveDependenciesAndDelete()}
        />
      )}
    </main>
  );
}

function Welcome({
  runtimeState,
  recentCanvasId,
  busy,
  notice,
  onCreate,
  onOpen,
}: {
  runtimeState: RuntimeState;
  recentCanvasId: string | null;
  busy: string | null;
  notice: Notice;
  onCreate: (title: string) => Promise<void>;
  onOpen: (canvasId: string) => Promise<void>;
}) {
  const available = runtimeState === "ready";
  return (
    <main className="welcome-shell">
      <section className="welcome-copy" aria-labelledby="page-title">
        <p className="eyebrow">Evidence-native opportunity canvas</p>
        <h1 id="page-title">Build the argument before the product.</h1>
        <p className="lede">
          Turn goals, builder constraints, and public evidence into a graph you
          can inspect, challenge, branch, and improve.
        </p>
        <div
          className={`runtime runtime--${runtimeState}`}
          role="status"
          aria-live="polite"
        >
          <span className="runtime__dot" aria-hidden="true" />
          {runtimeState === "checking" && "Connecting to the workspace…"}
          {runtimeState === "ready" && "Workspace ready"}
          {runtimeState === "unavailable" && "Workspace unavailable"}
        </div>
      </section>

      <section
        className="welcome-actions"
        aria-label="Open a Proofgraph canvas"
      >
        <div className="action-card action-card--primary">
          <p className="card-index">01</p>
          <h2>Create a canvas</h2>
          <p>
            Start with a goal, then make every constraint and connection
            explicit.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const title = String(
                new FormData(event.currentTarget).get("title") ?? "",
              ).trim();
              if (title) void onCreate(title);
            }}
          >
            <label htmlFor="new-canvas-title">Canvas title</label>
            <input
              id="new-canvas-title"
              name="title"
              placeholder="Solo founder opportunity map"
              required
            />
            <button
              type="submit"
              className="primary-button"
              disabled={!available || busy !== null}
            >
              Create canvas
            </button>
          </form>
        </div>

        <div className="action-card">
          <p className="card-index">02</p>
          <h2>Open an existing canvas</h2>
          <p>
            Paste a canvas ID to reload its graph, positions, and entity
            versions.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const canvasId = String(
                new FormData(event.currentTarget).get("canvasId") ?? "",
              ).trim();
              if (canvasId) void onOpen(canvasId);
            }}
          >
            <label htmlFor="canvas-id">Canvas ID</label>
            <input
              id="canvas-id"
              name="canvasId"
              placeholder="00000000-0000-0000-0000-000000000000"
              required
            />
            <button
              type="submit"
              className="secondary-button"
              disabled={!available || busy !== null}
            >
              Open canvas
            </button>
          </form>
          {recentCanvasId && (
            <button
              type="button"
              className="recent-button"
              disabled={!available || busy !== null}
              onClick={() => void onOpen(recentCanvasId)}
            >
              Reopen recent canvas <span>{recentCanvasId.slice(0, 8)}…</span>
            </button>
          )}
        </div>
        {notice && (
          <div
            className={`notice notice--${notice.tone}`}
            role={notice.tone === "error" ? "alert" : "status"}
          >
            {notice.message}
          </div>
        )}
      </section>
    </main>
  );
}

function NodeCard({
  node,
  selected,
  onSelect,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onPointerCancel,
}: {
  node: GraphNode & { position: Position };
  selected: boolean;
  onSelect: () => void;
  onPointerDown: (event: ReactPointerEvent<HTMLButtonElement>) => void;
  onPointerMove: (event: ReactPointerEvent<HTMLButtonElement>) => void;
  onPointerUp: (event: ReactPointerEvent<HTMLButtonElement>) => void;
  onPointerCancel: (event: ReactPointerEvent<HTMLButtonElement>) => void;
}) {
  return (
    <article
      className={`graph-node graph-node--${node.kind}${selected ? " graph-node--selected" : ""}${node.stale ? " graph-node--stale" : ""}`}
      style={{ left: node.position.x, top: node.position.y }}
      onClick={onSelect}
      data-testid={`node-${node.id}`}
    >
      <button
        type="button"
        className="graph-node__handle"
        aria-label={`Move ${node.title}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
      >
        <span>{NODE_KIND_LABELS[node.kind]}</span>
        <span aria-hidden="true">•••</span>
      </button>
      <div className="graph-node__content">
        <h3>{node.title}</h3>
        {node.body && <p>{node.body}</p>}
        {node.kind === "constraint" && (
          <span className="node-status">
            {String(node.metadata.context_scope ?? "global")}
            {node.metadata.pinned === true ? " · pinned" : ""}
          </span>
        )}
        {node.stale && (
          <span className="node-status node-status--stale">
            Needs regeneration
          </span>
        )}
      </div>
    </article>
  );
}

function EdgeConnection({
  edge,
  nodes,
  selected,
  onSelect,
}: {
  edge: GraphEdge;
  nodes: Array<GraphNode & { position: Position }>;
  selected: boolean;
  onSelect: () => void;
}) {
  const source = nodes.find((node) => node.id === edge.source_node_id);
  const target = nodes.find((node) => node.id === edge.target_node_id);
  if (!source || !target) return null;
  const start = { x: source.position.x + 122, y: source.position.y + 58 };
  const end = { x: target.position.x + 122, y: target.position.y + 58 };
  const deltaX = end.x - start.x;
  const deltaY = end.y - start.y;
  const length = Math.hypot(deltaX, deltaY);
  const angle = Math.atan2(deltaY, deltaX) * (180 / Math.PI);
  const lineStyle = {
    left: start.x,
    top: start.y,
    width: length,
    transform: `rotate(${angle}deg)`,
  } as CSSProperties;
  const labelStyle = {
    left: start.x + deltaX / 2,
    top: start.y + deltaY / 2,
  } as CSSProperties;
  return (
    <>
      <div
        className={`graph-edge graph-edge--${edge.kind}${selected ? " graph-edge--selected" : ""}`}
        style={lineStyle}
      />
      <button
        type="button"
        className={`edge-label edge-label--${edge.kind}${selected ? " edge-label--selected" : ""}`}
        style={labelStyle}
        onClick={(event) => {
          event.stopPropagation();
          onSelect();
        }}
        aria-label={`${EDGE_KIND_LABELS[edge.kind]} connection`}
      >
        {EDGE_KIND_LABELS[edge.kind]}
      </button>
    </>
  );
}

function NodeInspector({
  node,
  nodes,
  disabled,
  onSave,
  onDelete,
}: {
  node: GraphNode;
  nodes: GraphNode[];
  disabled: boolean;
  onSave: (input: NodeEditInput) => void;
  onDelete: () => void;
}) {
  const initialScope =
    node.metadata.context_scope === "branch" ? "branch" : "global";
  const [scope, setScope] = useState<"global" | "branch">(initialScope);
  const branchRoots = nodes.filter(
    (candidate) => candidate.id !== node.id && isBranchRoot(candidate),
  );
  return (
    <div>
      <div className="inspector-heading">
        <div>
          <p className="inspector-kicker">{NODE_KIND_LABELS[node.kind]}</p>
          <h2>Edit node</h2>
        </div>
        <span className="version-badge">v{node.version}</span>
      </div>
      <form
        className="inspector-form"
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          onSave({
            title: String(form.get("title") ?? "").trim(),
            body: String(form.get("body") ?? ""),
            contextScope: node.kind === "constraint" ? scope : undefined,
            pinned:
              node.kind === "constraint"
                ? form.get("pinned") === "on"
                : undefined,
            branchRootNodeId:
              node.kind === "constraint" && scope === "branch"
                ? String(form.get("branchRootNodeId") ?? "")
                : node.kind === "constraint"
                  ? null
                  : undefined,
          });
        }}
      >
        <label htmlFor="node-title">Title</label>
        <input
          id="node-title"
          name="title"
          defaultValue={node.title}
          required
        />
        <label htmlFor="node-body">Body</label>
        <textarea
          id="node-body"
          name="body"
          defaultValue={node.body ?? ""}
          rows={6}
        />

        {node.kind === "constraint" && (
          <fieldset>
            <legend>Constraint context</legend>
            <label htmlFor="constraint-scope">Scope</label>
            <select
              id="constraint-scope"
              name="scope"
              value={scope}
              onChange={(event) =>
                setScope(event.target.value as "global" | "branch")
              }
            >
              <option value="global">Global</option>
              <option value="branch">Branch</option>
            </select>
            {scope === "branch" && (
              <>
                <label htmlFor="branch-root">Branch root</label>
                <select
                  id="branch-root"
                  name="branchRootNodeId"
                  defaultValue={node.branch_root_node_id ?? ""}
                  required
                >
                  <option value="" disabled>
                    Select a strategy, claim, or opportunity
                  </option>
                  {branchRoots.map((root) => (
                    <option key={root.id} value={root.id}>
                      {NODE_KIND_LABELS[root.kind]} · {root.title}
                    </option>
                  ))}
                </select>
                {branchRoots.length === 0 && (
                  <p className="field-hint">
                    Add a strategy, claim, or opportunity before using branch
                    scope.
                  </p>
                )}
              </>
            )}
            <label className="checkbox-row">
              <input
                type="checkbox"
                name="pinned"
                defaultChecked={node.metadata.pinned === true}
              />
              Include this constraint automatically when its scope applies
            </label>
          </fieldset>
        )}

        <div className="inspector-actions">
          <button type="submit" className="primary-button" disabled={disabled}>
            Save node
          </button>
          <button
            type="button"
            className="danger-button"
            disabled={disabled}
            onClick={onDelete}
          >
            Delete node
          </button>
        </div>
      </form>
    </div>
  );
}

function EdgeInspector({
  edge,
  nodes,
  disabled,
  onSave,
  onDelete,
}: {
  edge: GraphEdge;
  nodes: GraphNode[];
  disabled: boolean;
  onSave: (input: EdgeInput) => void;
  onDelete: () => void;
}) {
  return (
    <div>
      <div className="inspector-heading">
        <div>
          <p className="inspector-kicker">Connection</p>
          <h2>Edit edge</h2>
        </div>
        <span className="version-badge">v{edge.version}</span>
      </div>
      <EdgeForm
        nodes={nodes}
        disabled={disabled}
        initial={{
          sourceNodeId: edge.source_node_id,
          targetNodeId: edge.target_node_id,
          kind: edge.kind,
        }}
        submitLabel="Save connection"
        onSubmit={onSave}
      />
      <button
        type="button"
        className="danger-button danger-button--full"
        disabled={disabled}
        onClick={onDelete}
      >
        Delete connection
      </button>
    </div>
  );
}

function NodeDialog({
  nodes,
  disabled,
  onClose,
  onSubmit,
}: {
  nodes: GraphNode[];
  disabled: boolean;
  onClose: () => void;
  onSubmit: (input: NewNodeInput) => void;
}) {
  const [kind, setKind] = useState<NodeKind>("goal");
  const [scope, setScope] = useState<"global" | "branch">("global");
  const branchRoots = nodes.filter(isBranchRoot);
  return (
    <Modal title="Add a typed node" onClose={onClose}>
      <form
        className="dialog-form"
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          onSubmit({
            kind,
            title: String(form.get("title") ?? "").trim(),
            body: String(form.get("body") ?? ""),
            metadata:
              kind === "constraint"
                ? { context_scope: scope, pinned: form.get("pinned") === "on" }
                : {},
            branchRootNodeId:
              kind === "constraint" && scope === "branch"
                ? String(form.get("branchRootNodeId") ?? "")
                : null,
          });
        }}
      >
        <label htmlFor="new-node-kind">Type</label>
        <select
          id="new-node-kind"
          value={kind}
          onChange={(event) => setKind(event.target.value as NodeKind)}
        >
          {CREATABLE_NODE_KINDS.map((value) => (
            <option key={value} value={value}>
              {NODE_KIND_LABELS[value]}
            </option>
          ))}
        </select>
        <label htmlFor="new-node-title">Title</label>
        <input id="new-node-title" name="title" required autoFocus />
        <label htmlFor="new-node-body">Body</label>
        <textarea id="new-node-body" name="body" rows={4} />
        {kind === "constraint" && (
          <fieldset>
            <legend>Constraint context</legend>
            <label htmlFor="new-constraint-scope">Scope</label>
            <select
              id="new-constraint-scope"
              value={scope}
              onChange={(event) =>
                setScope(event.target.value as "global" | "branch")
              }
            >
              <option value="global">Global</option>
              <option value="branch">Branch</option>
            </select>
            {scope === "branch" && (
              <>
                <label htmlFor="new-branch-root">Branch root</label>
                <select
                  id="new-branch-root"
                  name="branchRootNodeId"
                  required
                  defaultValue=""
                >
                  <option value="" disabled>
                    Select a root node
                  </option>
                  {branchRoots.map((root) => (
                    <option key={root.id} value={root.id}>
                      {NODE_KIND_LABELS[root.kind]} · {root.title}
                    </option>
                  ))}
                </select>
              </>
            )}
            <label className="checkbox-row">
              <input type="checkbox" name="pinned" /> Pin when this scope
              applies
            </label>
          </fieldset>
        )}
        <div className="dialog-actions">
          <button type="button" className="quiet-button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary-button" disabled={disabled}>
            Add node
          </button>
        </div>
      </form>
    </Modal>
  );
}

function EdgeDialog({
  nodes,
  disabled,
  onClose,
  onSubmit,
}: {
  nodes: GraphNode[];
  disabled: boolean;
  onClose: () => void;
  onSubmit: (input: EdgeInput) => void;
}) {
  return (
    <Modal title="Connect two nodes" onClose={onClose}>
      <EdgeForm
        nodes={nodes}
        disabled={disabled}
        submitLabel="Add connection"
        onSubmit={onSubmit}
      />
    </Modal>
  );
}

function EdgeForm({
  nodes,
  disabled,
  initial,
  submitLabel,
  onSubmit,
}: {
  nodes: GraphNode[];
  disabled: boolean;
  initial?: EdgeInput;
  submitLabel: string;
  onSubmit: (input: EdgeInput) => void;
}) {
  return (
    <form
      className="dialog-form"
      onSubmit={(event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        onSubmit({
          sourceNodeId: String(form.get("sourceNodeId") ?? ""),
          targetNodeId: String(form.get("targetNodeId") ?? ""),
          kind: String(form.get("kind") ?? "supports") as EdgeKind,
        });
      }}
    >
      <label htmlFor={`source-${initial?.sourceNodeId ?? "new"}`}>Source</label>
      <select
        id={`source-${initial?.sourceNodeId ?? "new"}`}
        name="sourceNodeId"
        defaultValue={initial?.sourceNodeId ?? ""}
        required
      >
        <option value="" disabled>
          Select a node
        </option>
        {nodes.map((node) => (
          <option key={node.id} value={node.id}>
            {NODE_KIND_LABELS[node.kind]} · {node.title}
          </option>
        ))}
      </select>
      <label htmlFor={`edge-kind-${initial?.sourceNodeId ?? "new"}`}>
        Relationship
      </label>
      <select
        id={`edge-kind-${initial?.sourceNodeId ?? "new"}`}
        name="kind"
        defaultValue={initial?.kind ?? "supports"}
      >
        {EDGE_KINDS.map((kind) => (
          <option key={kind} value={kind}>
            {EDGE_KIND_LABELS[kind]}
          </option>
        ))}
      </select>
      <label htmlFor={`target-${initial?.targetNodeId ?? "new"}`}>Target</label>
      <select
        id={`target-${initial?.targetNodeId ?? "new"}`}
        name="targetNodeId"
        defaultValue={initial?.targetNodeId ?? ""}
        required
      >
        <option value="" disabled>
          Select a node
        </option>
        {nodes.map((node) => (
          <option key={node.id} value={node.id}>
            {NODE_KIND_LABELS[node.kind]} · {node.title}
          </option>
        ))}
      </select>
      <button type="submit" className="primary-button" disabled={disabled}>
        {submitLabel}
      </button>
    </form>
  );
}

function DeleteConflictDialog({
  conflict,
  disabled,
  onCancel,
  onResolve,
}: {
  conflict: DeleteConflict;
  disabled: boolean;
  onCancel: () => void;
  onResolve: () => void;
}) {
  const edgeCount = conflict.details.incident_edges.length;
  const constraintCount = conflict.details.referencing_constraints.length;
  return (
    <Modal title="Resolve dependencies first" onClose={onCancel}>
      <div className="conflict-copy">
        <p>
          <strong>{conflict.node.title}</strong> cannot be deleted while other
          graph records refer to it.
        </p>
        <ul>
          {edgeCount > 0 && (
            <li>
              {edgeCount} incident{" "}
              {edgeCount === 1 ? "connection" : "connections"} will be
              explicitly deleted.
            </li>
          )}
          {constraintCount > 0 && (
            <li>
              {constraintCount} branch{" "}
              {constraintCount === 1 ? "constraint" : "constraints"} will be
              explicitly changed to global scope.
            </li>
          )}
        </ul>
        <p className="field-hint">
          Each prerequisite is recorded as its own audited graph operation
          before the node deletion is retried.
        </p>
      </div>
      <div className="dialog-actions">
        <button type="button" className="quiet-button" onClick={onCancel}>
          Keep node
        </button>
        <button
          type="button"
          className="danger-button"
          disabled={disabled}
          onClick={onResolve}
        >
          Resolve and delete
        </button>
      </div>
    </Modal>
  );
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
      >
        <header>
          <div>
            <p className="inspector-kicker">Graph operation</p>
            <h2 id="modal-title">{title}</h2>
          </div>
          <button
            type="button"
            className="modal-close"
            aria-label="Close dialog"
            onClick={onClose}
          >
            ×
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
