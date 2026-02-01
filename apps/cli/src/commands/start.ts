import chalk from "chalk";
import ora from "ora";
import { execa } from "execa";
import { existsSync } from "fs";
import { confirm, input, password } from "@inquirer/prompts";

import { envFileExists, readCurrentEnv } from "../utils/env.js";
import { getRepoRoot, getWebDir } from "../utils/paths.js";

interface StartOptions {
  web?: boolean;
  build?: boolean;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const processes: any[] = [];
let shuttingDown = false;

function setupSignalHandlers(): void {
  const shutdown = async () => {
    if (shuttingDown) return;
    shuttingDown = true;

    console.log();
    console.log(chalk.yellow("Shutting down..."));

    // Kill local processes (web dev server)
    for (const proc of processes) {
      try {
        proc.kill("SIGTERM");
      } catch {
        // Ignore
      }
    }

    console.log(chalk.green("✓") + " Services stopped");
    console.log(chalk.dim("  Run 'docker compose down' to stop infrastructure"));
    process.exit(0);
  };

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

function getServicesToStart(authEnabled: boolean): string[] {
  // Core services always needed
  const services = [
    "postgres",
    "redis",
    "qdrant",
    "rustfs",
    "api",
    "celery-worker",
    "flower",
    "celery-exporter",
    "prometheus",
    "grafana",
  ];

  // Add Authentik services only when auth is enabled
  if (authEnabled) {
    services.push(
      "authentik-db",
      "authentik-redis",
      "authentik-server",
      "authentik-worker"
    );
  }

  return services;
}

async function startDockerServices(build: boolean, authEnabled: boolean): Promise<boolean> {
  const services = getServicesToStart(authEnabled);
  const serviceList = authEnabled ? "all services" : "core services (auth disabled)";
  const spinner = ora(`Starting ${serviceList} in Docker...`).start();

  try {
    const args = ["compose", "up", "-d", ...services];
    if (build) {
      args.push("--build");
    }

    await execa("docker", args, {
      cwd: getRepoRoot(),
      stdio: "pipe",
    });

    spinner.succeed(`Docker services started (${authEnabled ? "with Authentik" : "no auth"})`);
    return true;
  } catch (error) {
    spinner.fail("Failed to start Docker services");
    console.log(chalk.dim(`  ${error}`));
    return false;
  }
}

async function waitForApi(): Promise<boolean> {
  const spinner = ora("Waiting for API to be ready...").start();

  for (let i = 0; i < 90; i++) {
    try {
      const response = await fetch("http://localhost:8000/health", {
        signal: AbortSignal.timeout(2000),
      });
      if (response.ok) {
        spinner.succeed("API is ready (migrations applied)");
        return true;
      }
    } catch {
      // Not ready yet
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  spinner.fail("API did not become ready in time");
  console.log(chalk.yellow("  Check logs with: docker compose logs api"));
  return false;
}

function getErrorDetail(error: unknown): string | undefined {
  if (!error) {
    return undefined;
  }
  if (typeof error === "string") {
    return error;
  }
  if (typeof error === "object") {
    const err = error as {
      stderr?: string;
      stdout?: string;
      shortMessage?: string;
      message?: string;
    };
    const detail =
      err.stderr?.trim() ||
      err.stdout?.trim() ||
      err.shortMessage?.trim() ||
      err.message?.trim();
    return detail || undefined;
  }
  return undefined;
}

async function waitForAuthentik(): Promise<boolean> {
  const spinner = ora("Waiting for Authentik to be ready...").start();
  let lastError: unknown;

  for (let i = 0; i < 45; i++) {
    try {
      await execa("docker", ["exec", "lifelog-authentik", "ak", "healthcheck"], {
        cwd: getRepoRoot(),
        stdio: "pipe",
        timeout: 10000,
      });
      spinner.succeed("Authentik is ready");
      return true;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }

  spinner.fail("Authentik did not become ready in time");
  const detail = getErrorDetail(lastError);
  if (detail) {
    console.log(chalk.dim(`  ${detail}`));
  }
  console.log(chalk.yellow("  Check logs with: docker compose logs authentik-server"));
  return false;
}

async function ensureStorageBuckets(): Promise<boolean> {
  const spinner = ora("Creating storage buckets...").start();

  try {
    const env = readCurrentEnv();
    // Only 'originals' bucket is used - previews/thumbnails are stored as paths within it
    const buckets = [env.BUCKET_ORIGINALS || "originals"];

    await execa(
      "docker",
      [
        "exec",
        "lifelog-api",
        "/app/.venv/bin/python",
        "-c",
        `
import boto3
from botocore.config import Config

s3 = boto3.client(
    's3',
    endpoint_url='http://rustfs:9000',
    aws_access_key_id='${env.S3_ACCESS_KEY_ID || "rustfs"}',
    aws_secret_access_key='${env.S3_SECRET_ACCESS_KEY || "rustfs123"}',
    region_name='${env.S3_REGION || "us-east-1"}',
    config=Config(s3={'addressing_style': 'path'})
)

buckets = ${JSON.stringify(buckets)}
for bucket in buckets:
    try:
        s3.create_bucket(Bucket=bucket)
        print(f'Created: {bucket}')
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f'Exists: {bucket}')
    except Exception as e:
        print(f'Error {bucket}: {e}')
`,
      ],
      {
        cwd: getRepoRoot(),
        stdio: "pipe",
      }
    );

    spinner.succeed("Storage buckets ready");
    return true;
  } catch (error) {
    spinner.warn("Could not create storage buckets");
    console.log(chalk.dim(`  ${error}`));
    return false;
  }
}

async function setupAuthentik(): Promise<boolean> {
  const spinner = ora("Configuring Authentik OAuth...").start();

  try {
    const setupScript = `${getRepoRoot()}/orchestration/setup-authentik-oauth.sh`;
    if (!existsSync(setupScript)) {
      spinner.warn("Authentik setup script not found, skipping");
      return false;
    }

    const env = readCurrentEnv();
    const authentikEnv: Record<string, string> = {};
    if (env.AUTHENTIK_BOOTSTRAP_EMAIL) {
      authentikEnv.AUTHENTIK_BOOTSTRAP_EMAIL = env.AUTHENTIK_BOOTSTRAP_EMAIL;
    }
    if (env.AUTHENTIK_BOOTSTRAP_PASSWORD) {
      authentikEnv.AUTHENTIK_BOOTSTRAP_PASSWORD = env.AUTHENTIK_BOOTSTRAP_PASSWORD;
    }

    await execa("bash", [setupScript], {
      cwd: getRepoRoot(),
      stdio: "pipe",
      env: {
        ...process.env,
        WEB_PORT: "3000",
        ...authentikEnv,
      },
    });

    spinner.succeed("Authentik OAuth configured");
    return true;
  } catch (error) {
    spinner.fail("Authentik setup failed");
    const detail = getErrorDetail(error);
    if (detail) {
      console.log(chalk.dim(`  ${detail}`));
      if (detail.includes("permission denied while trying to connect to the docker API")) {
        console.log(chalk.dim("  Docker socket access is required to configure Authentik."));
      }
    }
    console.log(chalk.yellow("  Run manually: bash orchestration/setup-authentik-oauth.sh"));
    return false;
  }
}

function isAuthEnabled(): boolean {
  const env = readCurrentEnv();
  return String(env.AUTH_ENABLED || "").toLowerCase() === "true";
}

async function createAuthentikUser(): Promise<boolean> {
  const wantsUser = await confirm({
    message: "Create a login user now?",
    default: true,
  });

  if (!wantsUser) {
    return false;
  }

  const email = await input({
    message: "User email:",
    validate: (v) => v.includes("@") || "Enter a valid email",
  });

  let userPassword = "";
  while (!userPassword || userPassword.length < 8) {
    userPassword = await password({
      message: "User password (min 8 chars):",
      mask: "*",
    });
    if (userPassword.length < 8) {
      console.log(chalk.red("  Password must be at least 8 characters"));
    }
  }

  const spinner = ora("Creating Authentik user...").start();

  try {
    await execa(
      "docker",
      [
        "exec",
        "-e",
        "OMNI_USER_EMAIL",
        "-e",
        "OMNI_USER_PASSWORD",
        "lifelog-authentik",
        "ak",
        "shell",
        "-c",
        `import os
from authentik.core.models import User

email = os.environ.get("OMNI_USER_EMAIL")
password = os.environ.get("OMNI_USER_PASSWORD")
username = email

user = User.objects.filter(username=username).first() or User.objects.filter(email=email).first()
if user:
    print("User already exists")
else:
    user = User.objects.create(username=username, email=email, name=email.split("@")[0])
    user.set_password(password)
    user.save()
    print("User created")
`,
      ],
      {
        cwd: getRepoRoot(),
        stdio: "pipe",
        env: {
          ...process.env,
          OMNI_USER_EMAIL: email,
          OMNI_USER_PASSWORD: userPassword,
        },
      }
    );

    spinner.succeed("Authentik user ready");
    return true;
  } catch (error) {
    spinner.warn("Could not create user (Authentik may still be starting)");
    console.log(chalk.dim(`  ${error}`));
    return false;
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function startWebDev(): Promise<any> {
  const webDir = getWebDir();

  if (!existsSync(webDir)) {
    console.log(chalk.dim("Skipping web (apps/web not found)"));
    return null;
  }

  if (!existsSync(`${webDir}/node_modules`)) {
    console.log(chalk.yellow("Installing web dependencies..."));
    try {
      await execa("npm", ["install"], { cwd: webDir, stdio: "inherit" });
    } catch (error) {
      console.log(chalk.red("✗") + ` Failed to install web dependencies: ${error}`);
      return null;
    }
  }

  console.log(chalk.cyan("Starting web dev server..."));

  try {
    const proc = execa("npm", ["run", "dev"], {
      cwd: webDir,
      stdio: "inherit",
      reject: false,
    });

    processes.push(proc);
    return proc;
  } catch (error) {
    console.log(chalk.red("✗") + ` Failed to start web server: ${error}`);
    return null;
  }
}

export async function start(options: StartOptions): Promise<void> {
  const includeWeb = options.web !== false;
  const shouldBuild = options.build !== false; // Build by default first time

  console.log();
  console.log(chalk.blue.bold("  OmniMemory"));
  console.log(chalk.dim("  ──────────"));
  console.log();

  // Check for .env
  if (!envFileExists()) {
    console.log(chalk.red("Configuration not found."));
    console.log("Run " + chalk.bold("omni setup") + " first.");
    process.exit(1);
  }

  // Setup signal handlers
  setupSignalHandlers();

  // Check auth status
  const authEnabled = isAuthEnabled();

  // Start Docker services (skip Authentik if auth disabled)
  if (!(await startDockerServices(shouldBuild, authEnabled))) {
    console.log(chalk.yellow("Make sure Docker is running and try again."));
    process.exit(1);
  }

  // Wait for API to be ready (migrations run on startup)
  console.log();
  const apiReady = await waitForApi();

  // Create storage buckets (idempotent)
  if (apiReady) {
    console.log();
    await ensureStorageBuckets();
  }

  // Setup Authentik OAuth (only when auth is enabled)
  if (authEnabled) {
    console.log();
    const authentikReady = await waitForAuthentik();
    if (!authentikReady) {
      console.log(chalk.yellow("  Authentik is not ready; aborting startup."));
      process.exit(1);
    }

    const setupOk = await setupAuthentik();
    if (!setupOk) {
      console.log(chalk.yellow("  Authentik OAuth setup failed; aborting startup."));
      process.exit(1);
    }

    // Optional Authentik user creation
    console.log();
    await createAuthentikUser();
  }

  // Start web dev server (runs locally with Node.js)
  if (includeWeb) {
    console.log();
    await startWebDev();
  }

  console.log();
  console.log(chalk.green.bold("  Services started!"));
  console.log();
  console.log("  API:     " + chalk.cyan("http://localhost:8000"));
  console.log("  Docs:    " + chalk.cyan("http://localhost:8000/docs"));
  if (includeWeb) {
    console.log("  Web:     " + chalk.cyan("http://localhost:3000"));
  }
  console.log("  Flower:  " + chalk.cyan("http://localhost:5555"));
  console.log();

  if (!apiReady) {
    console.log(chalk.yellow("  API may still be starting. Check: docker compose logs api"));
    console.log();
  }

  console.log(chalk.dim("  Press Ctrl+C to stop web server"));
  console.log(chalk.dim("  Run 'docker compose down' to stop all services"));
  console.log();

  // Keep running until interrupted
  if (includeWeb && processes.length > 0) {
    try {
      await processes[0];
    } catch {
      // Process exited
    }
  } else {
    // Just wait for Ctrl+C
    await new Promise(() => {});
  }
}
