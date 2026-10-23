# Aether v0.1 Release Acceptance Workload & Matrix

This directory contains the versioned, public-safe acceptance fixture, mind configurations, runner, and result matrix template for the **Aether Atelier v0.1** release.

## Directory Structure

```
acceptance/
├── README.md               # This specification
├── manifest.json           # SHA-256 fixture integrity hashes
├── matrix_template.json    # JSON template for the acceptance result matrix
├── runner.py               # CLI tool to prepare workspaces and run evaluator
├── fixture/
│   ├── agent_inputs/       # Frozen project files visible to agent minds
│   │   ├── project_a/      # Project A: Harbor settlement report (USD 2-decimal)
│   │   └── project_b/      # Project B: Multi-currency report (USD 2, JPY 0, KWD 3)
│   └── evaluator/          # Evaluator-only tests and reference answers
│       ├── grade.py        # Independent deterministic behavioral test runner
│       ├── reference.py    # Reference implementations
│       └── test_fixture.py # Workload verification tests
└── minds/                  # Public-safe demonstration minds
    ├── lone_master/        # Autonomous single-mind offline test case ("LedgerMaster")
    ├── lead/               # Atelier team orchestrator ("AtelierMaster")
    ├── author/             # Implementation specialist ("Scribe")
    └── reviewer/           # Independent reviewer ("Auditor")
```

## Workload Invariant

**Separation of Concerns:**
The evaluator scripts in `fixture/evaluator/` are strictly isolated from agent inputs. Agents receive only `fixture/agent_inputs/project_a/` or `fixture/agent_inputs/project_b/`. No reference implementations, grader code, or expected answers are ever mounted into agent roots or search indexes.

## Workflow Execution

### 1. Prepare Clean Workspace
```bash
python runner.py prepare-workspace --project A --dest C:/path/to/clean_workspaces/project_a
```

### 2. Initialize Acceptance Matrix
```bash
python runner.py matrix-init --out C:/path/to/evidence/acceptance_matrix_run01.json --version v0.1.0-alpha --layer clean_windows_install --machine Machine2
```

### 3. Run Independent Evaluator (Post-Commission)
After the agent minds complete work and close the commission, run the evaluator:
```bash
python runner.py grade --project A --repo C:/path/to/clean_workspaces/project_a/repository
```

### 4. Restart and Run Project B
Restart the Aether Atelier application. Issue the Project B commission. Verify that the agent retrieves the relevant lesson from Project A, handles the changed currency precision constraint (JPY 0, KWD 3), and passes independent grading:
```bash
python runner.py prepare-workspace --project B --dest C:/path/to/clean_workspaces/project_b
# Execute Project B in Atelier...
python runner.py grade --project B --repo C:/path/to/clean_workspaces/project_b/repository
```
