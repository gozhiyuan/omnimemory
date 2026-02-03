import chalk from "chalk";
import { getContainerStatus, type ContainerStatus } from "../utils/docker.js";
import { checkApiHealth } from "../utils/validate.js";

interface ServiceStatus {
  name: string;
  status: "running" | "stopped" | "unhealthy" | "unknown";
  url?: string;
  details?: string;
}

function formatStatus(status: ServiceStatus): string {
  let statusIcon: string;
  let statusColor: typeof chalk;

  switch (status.status) {
    case "running":
      statusIcon = "●";
      statusColor = chalk.green;
      break;
    case "stopped":
      statusIcon = "○";
      statusColor = chalk.dim;
      break;
    case "unhealthy":
      statusIcon = "●";
      statusColor = chalk.yellow;
      break;
    default:
      statusIcon = "?";
      statusColor = chalk.dim;
  }

  let line = `  ${statusColor(statusIcon)} ${status.name.padEnd(20)}`;

  if (status.url) {
    line += chalk.dim(` ${status.url}`);
  }

  if (status.details) {
    line += chalk.dim(` (${status.details})`);
  }

  return line;
}

function containerToServiceStatus(container: ContainerStatus): ServiceStatus {
  const name = container.name
    .replace(/^omnimemory[-_]/, "")
    .replace(/[-_]\d+$/, "");

  let status: ServiceStatus["status"] = "unknown";

  if (container.state === "running") {
    status = container.health === "unhealthy" ? "unhealthy" : "running";
  } else if (container.state === "exited" || container.state === "dead") {
    status = "stopped";
  }

  // Map known services to their URLs
  const urlMap: Record<string, string> = {
    postgres: "localhost:5432",
    redis: "localhost:6379",
    qdrant: "http://localhost:6333",
    flower: "http://localhost:5555",
    prometheus: "http://localhost:9090",
    grafana: "http://localhost:3001",
    authentik: "http://localhost:9002",
    rustfs: "http://localhost:9000",
  };

  return {
    name,
    status,
    url: urlMap[name.toLowerCase()],
    details: container.health,
  };
}

export async function status(): Promise<void> {
  console.log();
  console.log(chalk.blue.bold("  OmniMemory Status"));
  console.log(chalk.dim("  ─────────────────"));
  console.log();

  // Check API health
  console.log(chalk.bold("Application Services"));
  console.log();

  const apiHealthy = await checkApiHealth();
  console.log(
    formatStatus({
      name: "API Server",
      status: apiHealthy ? "running" : "stopped",
      url: "http://localhost:8000",
    })
  );

  // Web server (check if port 5173 is in use)
  let webRunning = false;
  try {
    const response = await fetch("http://localhost:5173", {
      signal: AbortSignal.timeout(2000),
    });
    webRunning = response.ok || response.status === 404;
  } catch {
    webRunning = false;
  }

  console.log(
    formatStatus({
      name: "Web Dev Server",
      status: webRunning ? "running" : "stopped",
      url: "http://localhost:5173",
    })
  );

  console.log();
  console.log(chalk.bold("Infrastructure (Docker)"));
  console.log();

  // Get Docker container status
  const containers = await getContainerStatus();

  if (containers.length === 0) {
    console.log(chalk.dim("  No containers running"));
    console.log(chalk.dim("  Run 'make dev-up' to start infrastructure"));
  } else {
    // Sort containers by name
    const sorted = containers.sort((a, b) => a.name.localeCompare(b.name));

    for (const container of sorted) {
      const serviceStatus = containerToServiceStatus(container);
      console.log(formatStatus(serviceStatus));
    }
  }

  // Summary
  console.log();

  const runningContainers = containers.filter((c) => c.state === "running").length;
  const totalContainers = containers.length;

  if (apiHealthy && runningContainers > 0) {
    console.log(
      chalk.green("✓") +
        ` ${runningContainers}/${totalContainers} containers running, API healthy`
    );
  } else if (runningContainers > 0) {
    console.log(
      chalk.yellow("!") +
        ` ${runningContainers}/${totalContainers} containers running, API not responding`
    );
  } else {
    console.log(chalk.dim("  Services not running. Run 'omni start' to launch."));
  }

  console.log();
}
