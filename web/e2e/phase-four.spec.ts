import { expect, test, type Page } from "@playwright/test";

type NodeResult = {
  node: { id: string; version: number };
};

async function csrfToken(page: Page): Promise<string> {
  const health = await page.request.get("/api/health");
  expect(health.ok()).toBe(true);
  const cookie = (await page.context().cookies()).find(
    (candidate) => candidate.name === "csrftoken",
  );
  expect(cookie).toBeDefined();
  return cookie!.value;
}

async function postOperation(
  page: Page,
  canvasId: string,
  csrf: string,
  operation: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const response = await page.request.post(
    `/api/canvases/${canvasId}/operations`,
    {
      headers: { "X-CSRFToken": csrf },
      data: { operation_key: crypto.randomUUID(), ...operation },
    },
  );
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as Record<string, unknown>;
}

async function addNode(
  page: Page,
  canvasId: string,
  csrf: string,
  kind: string,
  title: string,
): Promise<NodeResult["node"]> {
  const result = (await postOperation(page, canvasId, csrf, {
    op: "ADD_NODE",
    node: {
      kind,
      title,
      body: `${title} body`,
      metadata: {},
    },
  })) as NodeResult;
  return result.node;
}

async function addEdge(
  page: Page,
  canvasId: string,
  csrf: string,
  sourceNodeId: string,
  targetNodeId: string,
  kind: string,
): Promise<void> {
  await postOperation(page, canvasId, csrf, {
    op: "ADD_EDGE",
    edge: {
      source_node_id: sourceNodeId,
      target_node_id: targetNodeId,
      kind,
    },
  });
}

test("editing an upstream input visibly invalidates every dependent descendant", async ({
  page,
}) => {
  const csrf = await csrfToken(page);
  const createResponse = await page.request.post("/api/canvases", {
    headers: { "X-CSRFToken": csrf },
    data: { title: `Phase 4 browser verification ${crypto.randomUUID()}` },
  });
  expect(createResponse.ok(), await createResponse.text()).toBe(true);
  const created = (await createResponse.json()) as { canvas: { id: string } };
  const canvasId = created.canvas.id;

  try {
    const goal = await addNode(page, canvasId, csrf, "goal", "Original goal");
    const strategy = await addNode(
      page,
      canvasId,
      csrf,
      "strategy",
      "Dependent strategy",
    );
    const claim = await addNode(
      page,
      canvasId,
      csrf,
      "claim",
      "Dependent claim",
    );
    const opportunity = await addNode(
      page,
      canvasId,
      csrf,
      "opportunity",
      "Dependent opportunity",
    );
    const assumption = await addNode(
      page,
      canvasId,
      csrf,
      "assumption",
      "Dependent assumption",
    );

    await addEdge(page, canvasId, csrf, goal.id, strategy.id, "derived_from");
    await addEdge(page, canvasId, csrf, strategy.id, claim.id, "derived_from");
    await addEdge(page, canvasId, csrf, claim.id, opportunity.id, "supports");
    await addEdge(
      page,
      canvasId,
      csrf,
      opportunity.id,
      assumption.id,
      "derived_from",
    );

    await page.goto("/");
    await expect(page.getByText("Workspace ready")).toBeVisible();
    await page.getByLabel("Canvas ID").fill(canvasId);
    await page.getByRole("button", { name: "Open canvas" }).click();
    await expect(page.getByTestId(`node-${goal.id}`)).toBeVisible();

    await page.getByTestId(`node-${goal.id}`).click();
    await page.getByLabel("Title").fill("Updated upstream goal");
    await page.getByRole("button", { name: "Save node" }).click();
    await expect(page.getByText("Node saved.")).toBeVisible();

    for (const node of [strategy, claim, opportunity, assumption]) {
      await expect(
        page.getByTestId(`node-${node.id}`).getByText("Needs regeneration"),
      ).toBeVisible();
    }
    await expect(
      page.getByTestId(`node-${goal.id}`).getByText("Needs regeneration"),
    ).toHaveCount(0);

    await page.getByRole("button", { name: "Reload" }).click();
    await expect(page.getByText("Canvas reloaded.")).toBeVisible();
    await expect(
      page
        .getByTestId(`node-${opportunity.id}`)
        .getByText("Needs regeneration"),
    ).toBeVisible();
  } finally {
    const deleteResponse = await page.request.delete(
      `/api/canvases/${canvasId}`,
      {
        headers: { "X-CSRFToken": csrf },
      },
    );
    expect(deleteResponse.status()).toBe(204);
  }
});
