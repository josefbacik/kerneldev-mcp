# Guidelines for AI Assistants Working on This Project

This document provides guidelines for AI assistants (Claude, etc.) when making changes to the kerneldev-mcp project.

## File Organization Rules

### 1. Documentation

**Main Documentation** (keep in root):
- `README.md` - Main project documentation
- `QUICKSTART.md` - Quick start guide for users
- `TESTING.md` - Test results and validation
- `CHANGELOG.md` - Version history and changes
- `CLAUDE.md` - This file (guidelines for AI assistants)

**Implementation Notes** (goes in `docs/implementation/`):
- Implementation details and technical notes
- Architecture documentation
- Feature-specific deep dives
- Bug fixes and workarounds
- Any .md file explaining "how we built X"

**User Documentation** (goes in `docs/`):
- User guides and tutorials
- Quickstart guides for specific features
- Usage examples

**Never Create These Files**:
- Temporary .md files in the root (like WHAT_TO_TRY.md, TODO.md, NOTES.md)
- Session-specific documentation
- Personal notes or reminders

### 2. Test Files

**Unit Tests** (goes in `tests/`):
- Test individual components/modules
- Follow naming: `test_<module_name>.py`
- Example: `tests/test_config_manager.py`

**Integration Tests** (goes in `tests/integration/`):
- Test workflows with real kernels
- Test cross-component interactions
- Require actual kernel source or build artifacts
- Follow naming: `test_<feature>_integration.py`

**Never Put Tests In**:
- Project root directory
- Anywhere outside `tests/` directory
- With ad-hoc script names like `test-mcp-setup.sh`

### 3. Binary and Build Artifacts

**Never Commit**:
- Compiled binaries (`a.out`, `*.o`, `*.so`)
- Python bytecode (`*.pyc`, `__pycache__/`)
- IDE configurations (`.vscode/`, `.idea/`)
- Local config files (`.mcp.json`, `local.config`)
- Temporary scripts (`test-*.sh` unless properly placed)

**Update .gitignore** when new artifact types appear.

### 4. Examples

**Example Code** (goes in `examples/`):
- Usage examples
- Sample configurations
- Tutorial code

## Documentation Best Practices

### When to Create New Documentation

**DO create new docs when**:
- Adding a major new feature (goes in `docs/`)
- Writing user-facing guides (goes in `docs/`)
- Documenting complex implementation details (goes in `docs/implementation/`)

**DON'T create new docs for**:
- Temporary notes during development
- Session-specific information
- Questions or TODOs (use issue tracker instead)
- Information that belongs in existing docs

### Updating Existing Documentation

**Always prefer** updating existing documentation over creating new files:
- Add new features to README.md
- Update CHANGELOG.md for all significant changes
- Extend existing docs rather than duplicating

### Documentation Consolidation

When you notice multiple docs covering similar topics:
1. Merge them into a single, well-organized document
2. Update references
3. Delete redundant files
4. Commit with a clear message about consolidation

## Git Workflow

### Commit Messages

Follow this format:
```
<Short summary in imperative mood>

<Detailed explanation of what changed and why>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

### What to Commit

**Always commit**:
- Source code changes
- Test additions/updates
- Documentation updates
- Configuration template changes

**Never commit**:
- Build artifacts
- Temporary files
- Session-specific notes
- Local configuration

### Before Committing

1. Check `git status` for untracked files
2. Review if untracked files should be:
   - Added to git (permanent project files)
   - Added to .gitignore (artifacts)
   - Deleted (temporary files)
3. Ensure test files are in `tests/` or `tests/integration/`
4. Ensure documentation is properly organized

## File Lifecycle

### Creating Files

When creating a new file, ask:
1. **Is this permanent?** → Yes: Add to git. No: Add to .gitignore or don't create.
2. **Is this documentation?** → Use appropriate docs/ subdirectory.
3. **Is this a test?** → Put in tests/ or tests/integration/.
4. **Is this source code?** → Put in src/kerneldev_mcp/.
5. **Is this configuration?** → Put in src/config_templates/.

### Deleting Files

When encountering files that seem temporary or misplaced:
1. Check git history to understand their purpose
2. If they're temporary artifacts: delete them
3. If they contain useful info: merge into appropriate docs
4. If they're in wrong location: move to correct location

### Moving Files

When reorganizing:
1. Use `git mv` to preserve history
2. Update all references to moved files
3. Update documentation
4. Commit with clear message explaining reorganization

## Directory Structure Reference

```
kerneldev-mcp/
├── README.md              # Main documentation
├── QUICKSTART.md          # Quick start guide
├── TESTING.md             # Test results
├── CHANGELOG.md           # Version history
├── CLAUDE.md              # This file
├── docs/                  # User documentation
│   ├── *.md               # User guides and tutorials
│   └── implementation/    # Implementation details
│       └── *.md           # Technical notes and deep dives
├── examples/              # Usage examples
│   └── *.md               # Example documentation
├── src/
│   ├── kerneldev_mcp/     # Source code
│   │   └── *.py           # Python modules
│   └── config_templates/  # Configuration templates
│       ├── targets/       # Target configurations
│       ├── debug/         # Debug levels
│       └── fragments/     # Config fragments
├── tests/                 # Unit tests
│   ├── test_*.py          # Test modules
│   └── integration/       # Integration tests
│       └── test_*_integration.py
├── pyproject.toml         # Package configuration
└── .gitignore             # Git ignore patterns
```

## Common Mistakes to Avoid

1. ❌ Creating .md files in root for temporary notes
2. ❌ Leaving test scripts in root directory
3. ❌ Committing build artifacts or binaries
4. ❌ Creating duplicate documentation
5. ❌ Not updating .gitignore for new artifact types
6. ❌ Making local config files that get tracked by git

## When Making Changes

1. **Review existing structure** before creating new files
2. **Consolidate** rather than duplicate
3. **Organize** files into appropriate directories
4. **Update** .gitignore if new artifact types appear
5. **Document** significant changes in CHANGELOG.md
6. **Test** that organization doesn't break anything

## Questions to Ask Before Creating a File

1. Is this file permanent or temporary?
2. Does this information belong in an existing file?
3. Where in the directory structure does this belong?
4. Should this be tracked by git or ignored?
5. Will users or developers need this file?

## Checklist Before Committing

- [ ] No temporary files in working directory
- [ ] All tests in tests/ or tests/integration/
- [ ] No .md files in root except approved ones
- [ ] .gitignore covers all artifact types
- [ ] Documentation is consolidated and organized
- [ ] No duplicate information across files
- [ ] All new files are in appropriate directories

---

**Last Updated**: 2025-10-22
**Purpose**: Maintain clean, organized repository structure
