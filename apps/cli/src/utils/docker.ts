import { execa } from "execa";
import { getRepoRoot } from "./paths.js";

export interface ContainerStatus {
  name: string;
  state: string;
  health?: string;
}

/**
 * Get status of docker compose containers
 */
export async function getContainerStatus(): Promise<ContainerStatus[]> {
  try {
    const result = await execa(
      "docker",
      ["compose", "ps", "--format", "json"],
      {
        cwd: getRepoRoot(),
        timeout: 30000,
      }
    );

    const containers: ContainerStatus[] = [];

    // Output might be multiple JSON objects, one per line
    for (const line of result.stdout.split("\n")) {
      if (line.trim()) {
        try {
          const data = JSON.parse(line);
          containers.push({
            name: data.Name || data.Service || "unknown",
            state: data.State || "unknown",
            health: data.Health,
          });
        } catch {
          // Ignore JSON parse errors
        }
      }
    }

    return containers;
  } catch {
    return [];
  }
}

/**
 * Start docker compose services
 */
export async function startDockerServices(): Promise<boolean> {
  try {
    await execa("docker", ["compose", "up", "-d", "--remove-orphans"], {
      cwd: getRepoRoot(),
      stdio: "inherit",
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Stop docker compose services
 */
export async function stopDockerServices(volumes: boolean = false): Promise<boolean> {
  try {
    const args = ["compose", "down"];
    if (volumes) {
      args.push("-v");
    }
    await execa("docker", args, {
      cwd: getRepoRoot(),
      stdio: "inherit",
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Check if specific containers are running
 */
export async function areContainersRunning(
  names: string[]
): Promise<Record<string, boolean>> {
  const status = await getContainerStatus();
  const result: Record<string, boolean> = {};

  for (const name of names) {
    const container = status.find(
      (c) => c.name.includes(name) || c.name === name
    );
    result[name] = container?.state === "running";
  }

  return result;
}
