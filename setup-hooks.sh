#!/bin/bash
#
# Setup script to configure git hooks for the kerneldev-mcp project
#
# This script configures git to use the shared hooks directory,
# ensuring that unit tests pass before commits are allowed.

set -e

echo "Setting up git hooks for kerneldev-mcp..."

# Configure git to use the hooks directory
git config core.hooksPath hooks

echo ""
echo "✓ Git hooks configured successfully!"
echo ""
echo "The following hooks are now active:"
echo "  - pre-commit: Checks code formatting, linting, and runs unit tests"
echo ""
echo "If a hook fails:"
echo "  - For formatting: run 'ruff format .'"
echo "  - For linting: run 'ruff check . --fix'"
echo "  - For unit tests: fix the failing tests and try again"
echo "  - To bypass (not recommended): use 'git commit --no-verify'"
echo ""
