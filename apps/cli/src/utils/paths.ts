import { existsSync } from "fs";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

/**
 * Find the repository root by looking for marker files
 */
export function getRepoRoot(): string {
  let current = resolve(__dirname);

  // Walk up until we find .env.example or docker-compose.yml
  while (current !== "/") {
    if (
      existsSync(join(current, ".env.example")) ||
      existsSync(join(current, "docker-compose.yml"))
    ) {
      return current;
    }
    current = dirname(current);
  }

  // Fallback: assume we're in apps/cli/dist/utils
  return resolve(__dirname, "../../../..");
}

export function getApiDir(): string {
  return join(getRepoRoot(), "services", "api");
}

export function getWebDir(): string {
  return join(getRepoRoot(), "apps", "web");
}

export function getEnvPath(): string {
  return join(getRepoRoot(), ".env");
}

export function getEnvExamplePath(): string {
  return join(getRepoRoot(), ".env.example");
}

export function getWebEnvPath(): string {
  return join(getWebDir(), ".env.local");
}

export function getWebEnvExamplePath(): string {
  return join(getWebDir(), ".env.local.example");
}
