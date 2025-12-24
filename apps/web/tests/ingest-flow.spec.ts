import { expect, test } from '@playwright/test';

const imageBuffer = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=',
  'base64'
);

test('upload appears in timeline and dashboard responses', async ({ page }) => {
  await page.goto('/');

  await page.getByRole('button', { name: 'Ingest' }).click();

  const uploadUrlPromise = page.waitForResponse((response) =>
    response.url().includes('/storage/upload-url') && response.request().method() === 'POST'
  );
  const ingestPromise = page.waitForResponse((response) =>
    response.url().includes('/upload/ingest') && response.request().method() === 'POST'
  );

  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles({
    name: 'playwright-test.png',
    mimeType: 'image/png',
    buffer: imageBuffer,
  });

  await page.getByRole('button', { name: 'Start Processing' }).click();

  await expect(page.getByText('Batch uploaded & queued for processing!')).toBeVisible({
    timeout: 120_000,
  });

  const uploadUrlResponse = await uploadUrlPromise;
  await ingestPromise;
  const uploadMeta = await uploadUrlResponse.json();
  const storageKey = uploadMeta.key;

  await page.getByRole('button', { name: 'Timeline' }).click();
  const timelineResponse = await page.waitForResponse((response) =>
    response.url().includes('/timeline') && response.request().method() === 'GET'
  );
  const timelinePayload = await timelineResponse.json();
  const timelineHasItem = timelinePayload.some((day: any) =>
    day.items.some((item: any) => item.storage_key === storageKey)
  );
  expect(timelineHasItem).toBeTruthy();

  await page.getByRole('button', { name: 'Dashboard' }).click();
  const dashboardResponse = await page.waitForResponse((response) =>
    response.url().includes('/dashboard/stats') && response.request().method() === 'GET'
  );
  const dashboardPayload = await dashboardResponse.json();
  const dashboardHasItem = dashboardPayload.recent_items.some(
    (item: any) => item.storage_key === storageKey
  );
  expect(dashboardHasItem).toBeTruthy();
});
