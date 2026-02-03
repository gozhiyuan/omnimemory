import { execa } from "execa";

export interface PrerequisiteResult {
  name: string;
  installed: boolean;
  version?: string;
  running?: boolean;
  required: boolean;
  message?: string;
}

/**
 * Check if a command exists and get its version
 */
async function checkCommand(
  cmd: string,
  versionArgs: string[],
  versionRegex: RegExp
): Promise<string | null> {
  try {
    const result = await execa(cmd, versionArgs, { timeout: 10000 });
    const match = result.stdout.match(versionRegex);
    return match ? match[1] : "installed";
  } catch {
    return null;
  }
}

/**
 * Check if Docker daemon is running
 */
async function checkDockerRunning(): Promise<boolean> {
  try {
    await execa("docker", ["info"], { timeout: 10000 });
    return true;
  } catch {
    return false;
  }
}

/**
 * Check all prerequisites
 */
export async function checkPrerequisites(): Promise<PrerequisiteResult[]> {
  const results: PrerequisiteResult[] = [];

  // Docker (required)
  const dockerVersion = await checkCommand(
    "docker",
    ["--version"],
    /Docker version (\S+)/
  );
  const dockerRunning = dockerVersion ? await checkDockerRunning() : false;
  results.push({
    name: "Docker",
    installed: !!dockerVersion,
    version: dockerVersion?.replace(",", "") || undefined,
    running: dockerRunning,
    required: true,
    message: !dockerVersion
      ? "Install Docker Desktop"
      : !dockerRunning
        ? "Start Docker Desktop"
        : undefined,
  });

  // Python (required)
  const pythonVersion = await checkCommand(
    "python3",
    ["--version"],
    /Python (\S+)/
  );
  let pythonOk = false;
  if (pythonVersion) {
    const [major, minor] = pythonVersion.split(".").map(Number);
    pythonOk = major >= 3 && minor >= 11;
  }
  results.push({
    name: "Python 3.11+",
    installed: !!pythonVersion,
    version: pythonVersion || undefined,
    required: true,
    message: !pythonVersion
      ? "Install Python 3.11+"
      : !pythonOk
        ? "Upgrade to Python 3.11+"
        : undefined,
  });

  // Node.js (required for CLI and frontend)
  const nodeVersion = await checkCommand("node", ["--version"], /v?(\S+)/);
  let nodeOk = false;
  if (nodeVersion) {
    const major = parseInt(nodeVersion.split(".")[0], 10);
    nodeOk = major >= 18;
  }
  results.push({
    name: "Node.js 18+",
    installed: !!nodeVersion,
    version: nodeVersion || undefined,
    required: true,
    message: !nodeVersion
      ? "Install Node.js 18+"
      : !nodeOk
        ? "Upgrade to Node.js 18+"
        : undefined,
  });

  return results;
}

/**
 * Check if all required prerequisites are met
 */
export function allRequiredMet(results: PrerequisiteResult[]): boolean {
  return results
    .filter((r) => r.required)
    .every((r) => r.installed && (r.running === undefined || r.running));
}
