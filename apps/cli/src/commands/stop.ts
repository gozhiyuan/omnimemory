import chalk from "chalk";
import ora from "ora";
import { execa } from "execa";

import { getRepoRoot } from "../utils/paths.js";
import { stopDockerServices } from "../utils/docker.js";

interface StopOptions {
  volumes?: boolean;
}

export async function stop(options: StopOptions = {}): Promise<void> {
  console.log();
  console.log(chalk.blue.bold("  OmniMemory Stop"));
  console.log(chalk.dim("  ───────────────"));
  console.log();

  // Kill any running uvicorn/celery processes
  const spinner = ora("Stopping application processes...").start();

  try {
    // Kill uvicorn processes
    await execa("pkill", ["-f", "uvicorn.*app.main"], { reject: false });
    // Kill celery processes
    await execa("pkill", ["-f", "celery.*app.celery_app"], { reject: false });
    spinner.succeed("Application processes stopped");
  } catch {
    spinner.succeed("Application processes stopped");
  }

  // Stop Docker infrastructure
  console.log();
  const dockerSpinner = ora("Stopping Docker infrastructure...").start();

  const dockerOk = await stopDockerServices(Boolean(options.volumes));

  if (dockerOk) {
    dockerSpinner.succeed(
      options.volumes ? "Docker infrastructure stopped (volumes removed)" : "Docker infrastructure stopped"
    );
  } else {
    dockerSpinner.warn("Could not stop Docker infrastructure");
    console.log(
      chalk.dim(`  Run manually: docker compose down${options.volumes ? " -v" : ""}`)
    );
  }

  console.log();
  console.log(chalk.green("✓") + " All services stopped");
  console.log();
}
