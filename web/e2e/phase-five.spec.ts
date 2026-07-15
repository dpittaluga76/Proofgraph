import { expect, test } from "@playwright/test";

test("anonymous demo boots into the canonical seed and resets in one click", async ({
  page,
}) => {
  const bootstrapResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().endsWith("/api/demo/bootstrap"),
  );
  await page.goto("/?demo=1");
  const initial = (await (await bootstrapResponse).json()) as {
    canvas: { id: string; title: string };
  };

  await expect(
    page.getByRole("textbox", { name: "Canvas", exact: true }),
  ).toHaveValue("Security questionnaire opportunity");
  await expect(
    page.getByRole("heading", { name: "Reduce security questionnaire work" }),
  ).toBeVisible();
  await expect(page.getByText("Six-week MVP")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Approved evidence only" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Small technical team" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Open another canvas" }),
  ).toHaveCount(0);

  await page.getByRole("button", { name: "Open generation controls" }).click();
  await expect(page.getByLabel("Execution profile")).toHaveValue(
    "demo_hybrid_v1",
  );
  await page.getByLabel("Execution profile").selectOption("replay_v1");
  await expect(page.getByLabel("Execution profile")).toHaveValue("replay_v1");

  const resetResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/demo/reset"),
  );
  await page.getByRole("button", { name: "Reset demo" }).click();
  const reset = (await (await resetResponse).json()) as {
    canvas: { id: string; title: string };
  };

  expect(reset.canvas.id).not.toBe(initial.canvas.id);
  await expect(
    page.getByText(
      "Demo reset to a fresh isolated copy of the starting canvas.",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Reduce security questionnaire work" }),
  ).toBeVisible();

  const retired = await page.request.get(`/api/canvases/${initial.canvas.id}`);
  expect(retired.status()).toBe(404);
  expect((await retired.json()).error.code).toBe("resource_not_found");
});
