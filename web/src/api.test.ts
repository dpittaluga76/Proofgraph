import { afterEach, describe, expect, it, vi } from "vitest";

import { applyOperation, createCanvas } from "./api";
import type { GraphCanvas } from "./graph";

afterEach(() => {
  document.cookie = "csrftoken=; Max-Age=0; path=/";
  vi.unstubAllGlobals();
});

describe("API client", () => {
  it("sends Django's CSRF cookie on browser mutations", async () => {
    document.cookie = "csrftoken=browser-token; path=/";
    const graph: GraphCanvas = {
      id: "11111111-1111-4111-8111-111111111111",
      title: "Browser canvas",
      revision: 0,
      created_at: "2026-07-14T12:00:00+00:00",
      updated_at: "2026-07-14T12:00:00+00:00",
      nodes: [],
      edges: [],
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ canvas: graph }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await createCanvas("Browser canvas");

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.credentials).toBe("same-origin");
    expect((init.headers as Headers).get("X-CSRFToken")).toBe("browser-token");
  });

  it("retries an ambiguous operation response with the same envelope", async () => {
    const result = { canvas_revision: 1, node: { id: "node-id" } };
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => result,
      });
    vi.stubGlobal("fetch", fetchMock);
    const operation = {
      op: "ADD_NODE",
      operation_key: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      node: { kind: "goal", title: "Retry safely" },
    };

    await expect(
      applyOperation("11111111-1111-4111-8111-111111111111", operation),
    ).resolves.toEqual(result);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).body).toBe(
      JSON.stringify(operation),
    );
    expect((fetchMock.mock.calls[1]?.[1] as RequestInit).body).toBe(
      JSON.stringify(operation),
    );
  });

  it("reuses an unresolved operation key when the user submits again", async () => {
    const result = { canvas_revision: 1, edge: { id: "edge-id" } };
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockRejectedValueOnce(new TypeError("still offline"));
    vi.stubGlobal("fetch", fetchMock);
    const canvasId = "22222222-2222-4222-8222-222222222222";
    const original = {
      op: "ADD_EDGE",
      operation_key: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      edge: {
        source_node_id: "source",
        target_node_id: "target",
        kind: "supports",
      },
    };

    await expect(applyOperation(canvasId, original)).rejects.toThrow(
      "still offline",
    );

    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => result,
    });
    await applyOperation(canvasId, {
      ...original,
      operation_key: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    });

    const replayBody = JSON.parse(
      String((fetchMock.mock.calls[2]?.[1] as RequestInit).body),
    ) as { operation_key: string };
    expect(replayBody.operation_key).toBe(original.operation_key);
  });
});
