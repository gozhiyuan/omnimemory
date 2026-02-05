import chalk from "chalk";
import ora from "ora";
import { confirm, input, password, select } from "@inquirer/prompts";
import { execa } from "execa";
import { existsSync } from "fs";
import { chmod, cp, mkdir, readdir } from "fs/promises";
import path from "path";
import os from "os";

import {
  envFileExists,
  generateSecret,
  readCurrentEnv,
  readEnvExample,
  writeEnvFile,
  type EnvConfig,
} from "../utils/env.js";
import {
  getEnvExamplePath,
  getEnvPath,
  getRepoRoot,
  getWebDir,
  getWebEnvExamplePath,
  getWebEnvPath,
} from "../utils/paths.js";
import {
  allRequiredMet,
  checkPrerequisites,
  type PrerequisiteResult,
} from "../utils/prerequisites.js";
import {
  validateGeminiApiKey,
  validateOpenClawConnection,
} from "../utils/validate.js";

interface SetupOptions {
  force?: boolean;
  skipInfra?: boolean;
}

function printHeader(): void {
  console.log();
  console.log(chalk.blue.bold("  OmniMemory Setup"));
  console.log(chalk.dim("  ─────────────────"));
  console.log();
}

function printPrerequisites(results: PrerequisiteResult[]): void {
  console.log(chalk.bold("Checking prerequisites..."));
  console.log();

  for (const result of results) {
    let status: string;
    let info: string;

    if (result.installed) {
      if (result.running === false) {
        status = chalk.red("✗");
        info = chalk.red("not running");
      } else {
        status = chalk.green("✓");
        info = result.version ? `v${result.version}` : "installed";
      }
    } else {
      status = result.required ? chalk.red("✗") : chalk.dim("-");
      info = result.required
        ? chalk.red("not installed")
        : chalk.dim("not installed (optional)");
    }

    console.log(`  ${status} ${result.name} ${chalk.dim(info)}`);

    if (result.message && (!result.installed || result.running === false)) {
      console.log(`    ${chalk.dim(result.message)}`);
    }
  }

  console.log();
}

async function promptEnvValues(existing: EnvConfig): Promise<EnvConfig> {
  const values = { ...existing };

  console.log();
  console.log(chalk.bold("Configuration"));
  console.log(chalk.dim("─────────────"));
  console.log();

  // Gemini API key (required)
  console.log(
    chalk.cyan.bold("Gemini API Key") + chalk.dim(" (required for AI features)")
  );

  let geminiKey = "";
  const existingGeminiKey = values.GEMINI_API_KEY !== "your-gemini-key" ? values.GEMINI_API_KEY : "";

  while (!geminiKey) {
    if (existingGeminiKey) {
      console.log(chalk.dim(`  Current: ${existingGeminiKey.slice(0, 10)}...`));
    }

    geminiKey = await password({
      message: "Gemini API key:",
      mask: "*",
    });

    // Use existing if user pressed enter without input
    if (!geminiKey && existingGeminiKey) {
      geminiKey = existingGeminiKey;
    }

    if (!geminiKey) {
      console.log(chalk.red("  Gemini API key is required"));
      continue;
    }

    const spinner = ora("Validating API key...").start();
    const valid = await validateGeminiApiKey(geminiKey);

    if (valid) {
      spinner.succeed("API key validated");
    } else {
      spinner.warn("Could not validate key (will use anyway)");
    }
  }
  values.GEMINI_API_KEY = geminiKey;

  console.log();

  // Model selection
  console.log(chalk.bold("AI Model Configuration"));
  console.log();

  const promptModelChoices = [
    { name: "gemini-2.5-flash-lite (Fastest, lowest cost)", value: "gemini-2.5-flash-lite" },
    { name: "gemini-2.5-flash (Balanced speed and quality)", value: "gemini-2.5-flash" },
    { name: "gemini-2.5-pro (Higher quality)", value: "gemini-2.5-pro" },
    { name: "gemini-3-pro-preview (Latest, experimental)", value: "gemini-3-pro-preview" },
  ];

  const agentPromptModel = await select({
    message: "Agent prompt model (for chat/reasoning):",
    choices: promptModelChoices,
    default: values.AGENT_PROMPT_MODEL || "gemini-2.5-flash",
  });
  values.AGENT_PROMPT_MODEL = agentPromptModel;

  const videoUnderstandingModel = await select({
    message: "Video understanding model (for media analysis):",
    choices: promptModelChoices,
    default: values.VIDEO_UNDERSTANDING_MODEL || "gemini-2.5-flash",
  });
  values.VIDEO_UNDERSTANDING_MODEL = videoUnderstandingModel;

  const imageModelChoices = [
    { name: "gemini-2.5-flash-image (Fast image generation)", value: "gemini-2.5-flash-image" },
    { name: "gemini-3-pro-image-preview (Latest, experimental)", value: "gemini-3-pro-image-preview" },
  ];

  const agentImageModel = await select({
    message: "Image generation model:",
    choices: imageModelChoices,
    default: values.AGENT_IMAGE_MODEL || "gemini-2.5-flash-image",
  });
  values.AGENT_IMAGE_MODEL = agentImageModel;

  console.log();

  // Storage provider - always use local S3
  values.STORAGE_PROVIDER = "s3";

  console.log();

  // Google Photos integration (optional)
  const enableGooglePhotos = await confirm({
    message: "Enable Google Photos sync?",
    default: false,
  });

  if (enableGooglePhotos) {
    console.log();
    console.log(chalk.cyan.bold("Google Photos OAuth"));

    values.GOOGLE_PHOTOS_CLIENT_ID = await input({
      message: "Client ID:",
      default: values.GOOGLE_PHOTOS_CLIENT_ID || "",
    });

    const clientSecret = await password({
      message: "Client Secret:",
      mask: "*",
    });
    values.GOOGLE_PHOTOS_CLIENT_SECRET = clientSecret || values.GOOGLE_PHOTOS_CLIENT_SECRET || "";
  }

  console.log();

  // OpenClaw integration (optional)
  const enableOpenClaw = await confirm({
    message: "Connect to OpenClaw?",
    default: false,
  });

  if (enableOpenClaw) {
    console.log();
    console.log(chalk.cyan.bold("OpenClaw Integration"));

    const openclawUrl = await input({
      message: "OpenClaw Gateway URL:",
      default: values.OPENCLAW_GATEWAY_URL || "http://localhost:18789",
    });

    const spinner = ora("Checking connection...").start();
    const connected = await validateOpenClawConnection(openclawUrl);

    if (connected) {
      spinner.succeed("OpenClaw connection verified");
    } else {
      spinner.warn("Could not connect (will configure anyway)");
    }

    const syncMemory = await confirm({
      message: "Sync daily summaries to OpenClaw memory files?",
      default: true,
    });

    const openclawWorkspace = await input({
      message: "OpenClaw workspace path:",
      default: values.OPENCLAW_WORKSPACE || "~/.openclaw",
    });

    values.OPENCLAW_GATEWAY_URL = openclawUrl;
    values.OPENCLAW_ENABLED = "true";
    values.OPENCLAW_SYNC_MEMORY = syncMemory ? "true" : "false";
    values.OPENCLAW_WORKSPACE = openclawWorkspace;

    await installOpenClawSkills(openclawWorkspace);
  } else {
    // Make the disabled state explicit in the env output.
    values.OPENCLAW_ENABLED = "false";
    values.OPENCLAW_SYNC_MEMORY = "false";
  }

  console.log();

  // Authentication (optional with admin bootstrap)
  const enableAuth = await confirm({
    message: "Enable authentication (Authentik OIDC)?",
    default: false,
  });

  if (enableAuth) {
    values.AUTH_ENABLED = "true";

    // Generate secret if not set
    if (!values.AUTHENTIK_SECRET_KEY || values.AUTHENTIK_SECRET_KEY === "replace-with-32+char-random-string") {
      values.AUTHENTIK_SECRET_KEY = generateSecret(48);
    }

    console.log();

    // Optional admin bootstrap
    const bootstrapAdmin = await confirm({
      message: "Create initial admin user?",
      default: true,
    });

    if (bootstrapAdmin) {
      console.log();
      console.log(chalk.cyan.bold("Admin User Bootstrap"));

      values.AUTHENTIK_BOOTSTRAP_EMAIL = await input({
        message: "Admin email:",
        default: values.AUTHENTIK_BOOTSTRAP_EMAIL || "admin@localhost",
        validate: (v) => v.includes("@") || "Enter a valid email",
      });

      let adminPassword = "";
      while (!adminPassword || adminPassword.length < 8) {
        adminPassword = await password({
          message: "Admin password (min 8 chars):",
          mask: "*",
        });
        if (adminPassword.length < 8) {
          console.log(chalk.red("  Password must be at least 8 characters"));
        }
      }
      values.AUTHENTIK_BOOTSTRAP_PASSWORD = adminPassword;

      console.log(
        chalk.dim(
          "  Admin user will be created on first Authentik startup"
        )
      );
    }

    console.log(
      chalk.dim("  Run 'make authentik-up' to start Authentik")
    );
  } else {
    values.AUTH_ENABLED = "false";
  }

  console.log();

  return values;
}

function expandTilde(inputPath: string): string {
  if (!inputPath.startsWith("~")) {
    return inputPath;
  }
  return path.join(os.homedir(), inputPath.slice(1));
}

async function installOpenClawSkills(workspacePath: string): Promise<void> {
  try {
    const repoRoot = getRepoRoot();
    const sourceDir = path.join(repoRoot, "docs", "openclaw", "skills", "omnimemory");
    if (!existsSync(sourceDir)) {
      console.log(chalk.yellow("! OpenClaw skill templates not found in repo."));
      return;
    }

    const expanded = expandTilde(workspacePath);
    const skillsDir = path.join(expanded, "skills");
    const targetDir = path.join(skillsDir, "omnimemory");

    if (existsSync(targetDir)) {
      const overwrite = await confirm({
        message: `OpenClaw skills already exist at ${targetDir}. Overwrite?`,
        default: false,
      });
      if (!overwrite) {
        console.log(chalk.dim("  Skipped OpenClaw skill install."));
        return;
      }
    }

    await mkdir(skillsDir, { recursive: true });
    await cp(sourceDir, targetDir, { recursive: true, force: true });

    const entries = await readdir(targetDir, { withFileTypes: true });
    await Promise.all(
      entries
        .filter((entry) => entry.isFile() && entry.name.endsWith(".sh"))
        .map((entry) => chmod(path.join(targetDir, entry.name), 0o755))
    );

    console.log(chalk.green("✓") + ` Installed OpenClaw skills to ${targetDir}`);
  } catch (error) {
    console.log(chalk.yellow("! Failed to install OpenClaw skills:"), error);
  }
}

async function createEnvFile(values: EnvConfig): Promise<boolean> {
  try {
    const envPath = getEnvPath();
    const templatePath = getEnvExamplePath();

    writeEnvFile(envPath, values, templatePath);
    console.log(chalk.green("✓") + " Created .env");
    return true;
  } catch (error) {
    console.log(chalk.red("✗") + ` Failed to create .env: ${error}`);
    return false;
  }
}

async function createWebEnvFile(): Promise<boolean> {
  const webDir = getWebDir();

  if (!existsSync(webDir)) {
    console.log(chalk.dim("-") + " Skipping web env (apps/web not found)");
    return true;
  }

  const values: EnvConfig = {
    VITE_API_URL: "http://localhost:8000",
    VITE_OIDC_ISSUER_URL: "http://localhost:9002/application/o/omnimemory/",
    VITE_OIDC_CLIENT_ID: "omnimemory",
    VITE_OIDC_REDIRECT_URI: "http://localhost:3000/",
    VITE_OIDC_SCOPES: '"openid profile email offline_access"',
    VITE_OIDC_AUTH_URL: "http://localhost:9002/application/o/authorize/",
    VITE_OIDC_TOKEN_URL: "http://localhost:9002/application/o/token/",
    VITE_OIDC_LOGOUT_URL: "http://localhost:9002/application/o/omnimemory/end-session/",
    VITE_OIDC_POST_LOGOUT_REDIRECT_URI: "http://localhost:3000/",
  };

  try {
    const envPath = getWebEnvPath();
    const templatePath = getWebEnvExamplePath();

    writeEnvFile(envPath, values, existsSync(templatePath) ? templatePath : undefined);
    console.log(chalk.green("✓") + " Created apps/web/.env.local");
    return true;
  } catch (error) {
    console.log(chalk.red("✗") + ` Failed to create web env: ${error}`);
    return false;
  }
}

async function startInfrastructure(): Promise<boolean> {
  const spinner = ora("Starting infrastructure (docker compose)...").start();

  try {
    await execa("docker", ["compose", "up", "-d", "--remove-orphans"], {
      cwd: getRepoRoot(),
    });
    spinner.succeed("Infrastructure started");
    return true;
  } catch (error) {
    spinner.fail("Failed to start infrastructure");
    console.log(chalk.dim(`  ${error}`));
    return false;
  }
}

export async function setup(options: SetupOptions): Promise<void> {
  printHeader();

  // Check if already configured
  if (envFileExists() && !options.force) {
    console.log(chalk.yellow("Configuration already exists."));

    const overwrite = await confirm({
      message: "Overwrite existing configuration?",
      default: false,
    });

    if (!overwrite) {
      console.log();
      console.log("Run " + chalk.bold("omni start") + " to launch services.");
      return;
    }
  }

  // Prerequisites
  const prereqs = await checkPrerequisites();
  printPrerequisites(prereqs);

  if (!allRequiredMet(prereqs)) {
    console.log(
      chalk.red("Please install missing prerequisites and try again.")
    );
    process.exit(1);
  }

  // Load existing values if any
  let existingValues: EnvConfig = {};
  if (envFileExists()) {
    existingValues = readCurrentEnv();
  }

  // Load defaults from .env.example
  const defaults = readEnvExample();
  existingValues = { ...defaults, ...existingValues };

  // Interactive prompts
  const values = await promptEnvValues(existingValues);

  // Setup phase
  console.log();
  console.log(chalk.bold("Setting up..."));
  console.log();

  // Create env files
  if (!(await createEnvFile(values))) {
    process.exit(1);
  }

  await createWebEnvFile();

  // Start infrastructure
  if (!options.skipInfra) {
    console.log();
    const infraOk = await startInfrastructure();

    if (!infraOk) {
      console.log();
      console.log(chalk.yellow("You can start infrastructure manually with:"));
      console.log("  make dev-up");
      console.log();
    }
  }

  // Done
  console.log();
  console.log(chalk.green.bold("  Setup complete!"));
  console.log();
  console.log(
    "  Run " + chalk.bold("omni start") + " to launch the application."
  );
  console.log(
    "  Run " + chalk.bold("omni status") + " to check service health."
  );
  console.log();
}
