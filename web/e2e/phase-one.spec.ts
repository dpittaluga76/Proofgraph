import { expect, test, type Page } from "@playwright/test";

type OperationResult = {
  canvas_revision: number;
  node: { id: string; version: number; position_version: number };
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
): Promise<OperationResult> {
  const response = await page.request.post(
    `/api/canvases/${canvasId}/operations`,
    {
      headers: { "X-CSRFToken": csrf },
      data: { operation_key: crypto.randomUUID(), ...operation },
    },
  );
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as OperationResult;
}

test("canonical graph survives layout, drag, reload, and a concurrent stale edit", async ({
  page,
}) => {
  const csrf = await csrfToken(page);
  const createResponse = await page.request.post("/api/canvases", {
    headers: { "X-CSRFToken": csrf },
    data: { title: `Phase 1 browser verification ${crypto.randomUUID()}` },
  });
  expect(createResponse.ok(), await createResponse.text()).toBe(true);
  const created = (await createResponse.json()) as { canvas: { id: string } };
  const canvasId = created.canvas.id;

  try {
    const goal = await postOperation(page, canvasId, csrf, {
      op: "ADD_NODE",
      node: {
        kind: "goal",
        title: "Primary goal",
        body: "A defensible recurring-revenue opportunity.",
        metadata: { target_user: "Technical founder" },
        position: { x: 72, y: 72 },
      },
    });
    for (let index = 0; index < 7; index += 1) {
      await postOperation(page, canvasId, csrf, {
        op: "ADD_NODE",
        node: {
          kind: "constraint",
          title: `Constraint ${index + 1}`,
          body: `Canonical builder constraint ${index + 1}`,
          metadata: {
            category: `constraint_${index + 1}`,
            context_scope: "global",
            pinned: true,
          },
          position: {
            x: 72 + (index % 4) * 286,
            y: 248 + Math.floor(index / 4) * 176,
          },
        },
      });
    }

    await page.goto("/");
    await expect(page.getByText("Workspace ready")).toBeVisible();
    await page.getByLabel("Canvas ID").fill(canvasId);
    await page.getByRole("button", { name: "Open canvas" }).click();
    await expect(
      page.getByRole("textbox", { name: "Canvas", exact: true }),
    ).toHaveValue(/Phase 1 browser verification/);

    await page.getByRole("button", { name: "Auto-layout" }).click();
    await expect(page.getByText("Deterministic layout saved.")).toBeVisible();
    await expect
      .poll(() =>
        page
          .getByTestId("graph-surface")
          .evaluate((element) =>
            Number.parseFloat(getComputedStyle(element).height),
          ),
      )
      .toBeGreaterThan(900);

    const goalCard = page.getByTestId(`node-${goal.node.id}`);
    const moveHandle = page.getByRole("button", { name: "Move Primary goal" });
    await moveHandle.scrollIntoViewIfNeeded();
    const box = await moveHandle.boundingBox();
    expect(box).not.toBeNull();
    const dragResponse = page.waitForResponse((response) => {
      const request = response.request();
      if (
        request.method() !== "POST" ||
        !request.url().endsWith(`/api/canvases/${canvasId}/operations`)
      ) {
        return false;
      }
      const body = request.postDataJSON() as Record<string, unknown> | null;
      return body?.op === "MOVE_NODE" && body.node_id === goal.node.id;
    });
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      box!.x + box!.width / 2 + 40,
      box!.y + box!.height / 2 - 40,
      { steps: 5 },
    );
    await page.mouse.up();
    expect((await dragResponse).ok()).toBe(true);
    const persistedTop = await goalCard.evaluate((element) =>
      Number.parseFloat((element as HTMLElement).style.top),
    );
    expect(persistedTop).toBeGreaterThan(900);

    await page.getByRole("button", { name: "Reload" }).click();
    await expect(page.getByText("Canvas reloaded.")).toBeVisible();
    await expect
      .poll(() =>
        goalCard.evaluate((element) =>
          Number.parseFloat((element as HTMLElement).style.top),
        ),
      )
      .toBe(persistedTop);

    await goalCard.click();
    await expect(
      page.getByRole("heading", { name: "Edit node" }),
    ).toBeVisible();
    const concurrentUpdate = await postOperation(page, canvasId, csrf, {
      op: "UPDATE_NODE",
      node_id: goal.node.id,
      expected_version: goal.node.version,
      changes: { title: "Current server goal" },
    });
    expect(concurrentUpdate.node.version).toBe(goal.node.version + 1);

    await page.getByLabel("Title").fill("Stale local edit");
    await page.getByRole("button", { name: "Save node" }).click();
    await expect(page.getByRole("alert")).toContainText(
      "This item changed elsewhere. Reload the canvas and try again.",
    );
    await page.getByRole("button", { name: "Reload" }).click();
    await expect(page.getByText("Canvas reloaded.")).toBeVisible();
    await expect(page.getByLabel("Title")).toHaveValue("Current server goal");
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
