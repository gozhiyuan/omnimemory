/**
 * Validate Gemini API key by making a test request
 */
export async function validateGeminiApiKey(apiKey: string): Promise<boolean> {
  if (!apiKey || !apiKey.startsWith("AIza")) {
    return false;
  }

  try {
    const response = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models?key=${apiKey}`,
      { signal: AbortSignal.timeout(10000) }
    );
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Validate OpenClaw gateway connection
 */
export async function validateOpenClawConnection(url: string): Promise<boolean> {
  try {
    const normalizedUrl = url.replace(/\/$/, "");
    const response = await fetch(`${normalizedUrl}/health`, {
      signal: AbortSignal.timeout(5000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Check if API server is running
 */
export async function checkApiHealth(
  url: string = "http://localhost:8000"
): Promise<boolean> {
  try {
    const response = await fetch(`${url}/health`, {
      signal: AbortSignal.timeout(5000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Check if Redis is reachable (via API health endpoint)
 */
export async function checkRedisHealth(): Promise<boolean> {
  // Redis health is typically checked via the API
  return checkApiHealth();
}
