import { existsSync, readFileSync, writeFileSync } from "fs";
import { getEnvExamplePath, getEnvPath } from "./paths.js";

export interface EnvConfig {
  [key: string]: string;
}

/**
 * Check if .env file exists
 */
export function envFileExists(): boolean {
  return existsSync(getEnvPath());
}

/**
 * Read an env file and return key-value pairs
 */
export function readEnvFile(path: string): EnvConfig {
  if (!existsSync(path)) {
    return {};
  }

  const content = readFileSync(path, "utf-8");
  const result: EnvConfig = {};

  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (trimmed && !trimmed.startsWith("#") && trimmed.includes("=")) {
      const eqIndex = trimmed.indexOf("=");
      const key = trimmed.slice(0, eqIndex).trim();
      const value = trimmed.slice(eqIndex + 1).trim();
      result[key] = value;
    }
  }

  return result;
}

/**
 * Read .env.example and return defaults
 */
export function readEnvExample(): EnvConfig {
  return readEnvFile(getEnvExamplePath());
}

/**
 * Read current .env file
 */
export function readCurrentEnv(): EnvConfig {
  return readEnvFile(getEnvPath());
}

/**
 * Write env file, preserving structure from template if available
 */
export function writeEnvFile(
  path: string,
  values: EnvConfig,
  templatePath?: string
): void {
  if (templatePath && existsSync(templatePath)) {
    // Preserve template structure, update values
    const template = readFileSync(templatePath, "utf-8");
    const lines: string[] = [];
    const writtenKeys = new Set<string>();

    for (const line of template.split("\n")) {
      const trimmed = line.trim();

      // Check for commented-out env vars (# KEY=value)
      const commentedMatch = trimmed.match(/^#\s*([A-Z_][A-Z0-9_]*)=/);
      if (commentedMatch) {
        const key = commentedMatch[1];
        if (key in values && values[key]) {
          // Uncomment and set the value
          lines.push(`${key}=${values[key]}`);
          writtenKeys.add(key);
          continue;
        }
      }

      // Check for regular env vars (KEY=value)
      if (trimmed && !trimmed.startsWith("#") && trimmed.includes("=")) {
        const eqIndex = trimmed.indexOf("=");
        const key = trimmed.slice(0, eqIndex).trim();
        if (key in values) {
          lines.push(`${key}=${values[key]}`);
          writtenKeys.add(key);
        } else {
          lines.push(line);
        }
      } else {
        lines.push(line);
      }
    }

    // Add any new keys that weren't in the template
    for (const [key, value] of Object.entries(values)) {
      if (!writtenKeys.has(key) && value) {
        lines.push(`${key}=${value}`);
      }
    }

    writeFileSync(path, lines.join("\n"));
  } else {
    // Simple key=value format
    const lines = Object.entries(values).map(([key, value]) => `${key}=${value}`);
    writeFileSync(path, lines.join("\n") + "\n");
  }
}

/**
 * Generate a random string for secrets
 */
export function generateSecret(length: number = 32): string {
  const chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
  let result = "";
  for (let i = 0; i < length; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}
