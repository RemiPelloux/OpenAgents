---
name: open-toolbox
description: Autonomous tool discovery, evaluation, and integration for agent self-enhancement
version: 1.0.0
metadata:
  tags: [tool-discovery, self-improvement, mcp, marketplace, dynamic]
  category: meta
  related_skills: [open-mcp-scaffold, open-brain]
---

# Open Toolbox (Autonomous Tool Discovery & Integration)

Enable agents to discover, evaluate, and integrate new tools dynamically. Agents can search MCP registries, evaluate tool capabilities, test them in sandbox, and propose integration to the mesh.

## When to Use

- Agent needs capability not in current toolbox
- User requests feature requiring new tool
- Agent encounters task it cannot complete with existing tools
- Proactive capability expansion (agent identifies gap)

## When NOT to Use

- Task completable with existing tools (check `open-ecosystem-hub` first)
- Tool requires credentials agent doesn't have (escalate to human)
- Tool violates security policy (escalate to OpenSec)
- Tool is experimental/unstable (flag for human review)

## Prerequisites

- Access to MCP registry (GitHub, npm, PyPI, Docker Hub)
- Sandbox environment for tool testing (OpenCode worktree)
- OpenBrain for tool evaluation storage
- OpenOrchestrator for approval workflow
- OpenSec for security review

## Architecture

```
Agent identifies capability gap
  → OpenBrain (search existing tools + evaluations)
  → MCP Registry (discover candidates)
  → OpenCode (sandbox test)
  → OpenSec (security review)
  → OpenOrchestrator (approval)
  → OpenMesh-Wiring (integrate)
  → OpenBrain (store evaluation)
```

## Tool Discovery Workflow

### Step 1: Identify Gap

Agent recognizes it cannot complete a task:

```
Task: "Scrape data from example.com"
Current tools: [open-code, open-ticket, ...]
Missing: browser automation capability
Gap identified: need web scraping tool
```

### Step 2: Search Existing Tools

Query OpenBrain for prior evaluations:

```
search_knowledge(query="browser automation tool", domain="openos-tools")
```

Returns cached evaluations if tool was previously assessed.

### Step 3: Discover Candidates

Search MCP registries:

```
# GitHub MCP servers
search_github(query="MCP server browser automation", language="rust|typescript|python")

# npm packages
search_npm(query="mcp-server", keywords=["browser", "automation"])

# Docker Hub
search_docker(query="mcp-server", tags=["browser", "automation"])

# Official MCP registry
fetch_mcp_registry(category="browser")
```

### Step 4: Evaluate Candidates

For each candidate, assess:

| Criteria | Weight | How to Check |
|----------|--------|--------------|
| **Functionality** | 30% | Does it solve the gap? |
| **Reliability** | 20% | Stars, issues, last commit |
| **Security** | 20% | OpenSec review, no known CVEs |
| **Integration** | 15% | MCP stdio/HTTP, Docker-ready |
| **Maintenance** | 15% | Active development, docs |

### Step 5: Sandbox Test

Deploy in isolated OpenCode worktree:

```
1. Create worktree: orch/tool-eval-<name>
2. Install tool (npm install / docker pull)
3. Run test suite
4. Execute sample tasks
5. Measure performance (latency, memory)
6. Check for security issues (OpenSec scan)
```

### Step 6: Propose Integration

If evaluation passes:

```
POST /v1/goals
{
  "objective": "Integrate <tool-name> for <capability>",
  "required_skills": ["open-toolbox", "open-mcp-scaffold"],
  "approval_required": true,
  "evidence": {
    "evaluation_score": 85,
    "sandbox_test": "passed",
    "security_review": "clean",
    "use_case": "web scraping for product data"
  }
}
```

OpenOrchestrator routes to human for approval (if required).

### Step 7: Integrate

After approval:

```
1. Add to docker-compose.mesh.yml (if containerized)
2. Register MCP server in OpenAgents config
3. Create skill (open-mcp-scaffold)
4. Update agent profiles with new capability
5. Store evaluation in OpenBrain
6. Log to OpenRec
```

## Tool Evaluation Template

```markdown
# Tool Evaluation: <name>

## Metadata
- Source: <github/npm/docker url>
- Version: <version>
- License: <license>
- Last updated: <date>
- Stars/Downloads: <count>

## Functionality
- Purpose: <what it does>
- MCP tools: <list>
- Use cases: <scenarios>

## Integration
- Transport: stdio | HTTP | WebSocket
- Docker: yes | no
- Dependencies: <list>
- Config: <env vars / config files>

## Security
- OpenSec review: <pass/fail>
- Known CVEs: <none | list>
- Credential handling: <ephemeral | stored | none>
- Network access: <restricted | unrestricted>

## Performance
- Latency: <ms>
- Memory: <MB>
- Concurrent sessions: <count>

## Recommendation
- Score: <0-100>
- Verdict: integrate | reject | needs-review
- Notes: <rationale>
```

## Decision Rules

| Scenario | Action |
|----------|--------|
| Tool exists in OpenBrain evaluations | Use cached evaluation |
| Multiple candidates found | Evaluate top 3, pick highest score |
| Tool requires credentials | Escalate to human (decision gate) |
| Tool fails security review | Reject, search alternatives |
| Tool score < 70 | Flag for human review |
| Tool score ≥ 85 + security clean | Auto-approve (if policy allows) |
| No candidates found | Escalate to human (build custom) |

## Pitfalls

- **Don't** skip sandbox testing - always verify in isolation
- **Don't** ignore security review - OpenSec scan is mandatory
- **Don't** integrate without approval (unless auto-approve policy)
- **Don't** store evaluations in agent memory - use OpenBrain (shared)
- **Don't** assume tool works - test with real tasks
- **Don't** forget to log to OpenRec - audit trail required

## Verification

- [ ] Tool discovery searched OpenBrain first
- [ ] At least 3 candidates evaluated (if available)
- [ ] Sandbox test passed (functionality + performance)
- [ ] OpenSec security review clean
- [ ] Approval obtained (if required)
- [ ] Integration logged to OpenRec
- [ ] Evaluation stored in OpenBrain
- [ ] Agent profiles updated with new capability

## Security

- Tool discovery searches only trusted registries (GitHub, npm, PyPI, Docker Hub official)
- Sandbox testing in isolated worktree (no mesh access)
- OpenSec review mandatory before integration
- Approval gate for tools requiring credentials or network access
- All evaluations stored in OpenBrain (immutable audit trail)

## Integration with Mesh

```typescript
// OpenBrain tool evaluation storage
{
  "type": "tool.evaluation",
  "payload": {
    "name": "obscura",
    "version": "1.0.0",
    "score": 92,
    "use_cases": ["web scraping", "form automation"],
    "security_review": "clean",
    "integration_status": "approved"
  }
}

// OpenRec audit
{
  "type": "tool.integration.completed",
  "payload": {
    "tool": "obscura",
    "capability": "browser_automation",
    "profiles_updated": ["browser-automation", "web-scraper"],
    "approval_id": "..."
  }
}
```

## Self-Enhancement Loop

```
1. Agent encounters task it cannot complete
2. Identifies capability gap
3. Searches for tools (OpenBrain → registries)
4. Evaluates candidates (sandbox + security)
5. Proposes integration (OpenOrchestrator)
6. Integrates after approval (OpenMesh-Wiring)
7. Stores evaluation (OpenBrain)
8. Next agent benefits from cached evaluation
```

This creates a **flywheel effect**: each tool integration makes future agents more capable.

## Related Skills

- `open-mcp-scaffold` - Add MCP tools to mesh
- `open-brain` - Store/retrieve tool evaluations
- `open-sec` - Security review
- `open-orchestrator-ops` - Approval workflow
- `open-mesh-wiring` - Integration into mesh
