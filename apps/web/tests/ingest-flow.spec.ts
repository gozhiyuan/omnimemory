import { expect, test } from '@playwright/test';

const imageBuffer = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=',
  'base64'
);

const apiBaseUrl = process.env.VITE_API_URL || 'http://127.0.0.1:8000';

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

  await expect
    .poll(
      async () => {
        const response = await page.request.get(`${apiBaseUrl}/timeline`);
        const timelinePayload = await response.json();
        return timelinePayload.some((day: any) =>
          day.items.some((item: any) => item.storage_key === storageKey)
        );
      },
      { timeout: 120_000 }
    )
    .toBeTruthy();

  await expect
    .poll(
      async () => {
        const response = await page.request.get(`${apiBaseUrl}/dashboard/stats`);
        const dashboardPayload = await response.json();
        return dashboardPayload.recent_items.some(
          (item: any) => item.storage_key === storageKey
        );
      },
      { timeout: 120_000 }
    )
    .toBeTruthy();
});
