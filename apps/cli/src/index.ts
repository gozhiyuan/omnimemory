#!/usr/bin/env node

import { Command } from "commander";
import { setup } from "./commands/setup.js";
import { start } from "./commands/start.js";
import { stop } from "./commands/stop.js";
import { status } from "./commands/status.js";

const program = new Command();

program
  .name("omni")
  .description("OmniMemory CLI - Personal memory AI system")
  .version("0.1.0");

program
  .command("setup")
  .description("Interactive setup wizard for first-time configuration")
  .option("-f, --force", "Overwrite existing configuration")
  .option("--skip-infra", "Skip starting Docker infrastructure")
  .action(setup);

program
  .command("start")
  .description("Start all OmniMemory services")
  .option("--no-build", "Skip rebuilding Docker images")
  .option("--no-web", "Skip starting frontend dev server")
  .action(start);

program
  .command("stop")
  .description("Stop all OmniMemory services")
  .option("-v, --volumes", "Remove named volumes (destructive)")
  .action(stop);

program
  .command("status")
  .description("Show status of all services")
  .action(status);

program.parse();
