---
name: open-browser
description: Browser automation via Playwright MCP (official Microsoft headless browser)
version: 1.0.0
metadata:
  tags: [browser, automation, scraping, web, playwright, mcp]
  category: automation
  related_skills: [computer-use, open-code]
---

# Open Browser (Playwright MCP Integration)

Automate web interactions using Playwright, the official Microsoft headless browser with native MCP server support. Playwright provides robust browser automation with multi-browser support (Chromium, Firefox, WebKit).

## When to Use

- Web scraping and data extraction
- Form filling and submission
- Browser-based testing
- Web automation tasks requiring JavaScript execution
- PDF generation from web pages
- Screenshot capture
- Multi-browser testing

## When NOT to Use

- Desktop GUI automation (use `computer-use` skill)
- Simple HTTP requests (use REST API directly)
- File operations (use `open-code` skill)

## Prerequisites

- Playwright Docker container running (`docker-compose.mesh.yml` includes `playwright-mcp`)
- MCP server accessible at `http://playwright-mcp:3100` (container) or `http://localhost:3100` (host)
- Agent profile with `browser_automation` capability

## Architecture

```
OpenOrchestrator (plan with browser step)
  → OpenAgents (browser-automation profile)
  → MCP call to playwright-mcp-server
  → Playwright container (headless browser)
  → Results → OpenRec (audit) → OpenBrain (observation)
```

## Playwright MCP Tools

### Navigation

```
browser_navigate(url: string, waitUntil?: "load" | "domcontentloaded" | "networkidle")
```
Navigate to URL and wait for specified state.

### Page Interaction

```
browser_click(selector: string)
```
Click element matching CSS selector.

```
browser_fill(selector: string, value: string)
```
Fill input field with value (triggers change events).

```
browser_type(selector: string, text: string)
```
Type text into input field character by character.

```
browser_press_key(key: string, selector?: string)
```
Press keyboard key (optionally targeting specific element).

```
browser_select_option(selector: string, value: string)
```
Select dropdown option by value or text.

### Page State

```
browser_snapshot()
```
Returns: `{ url, title, text }` - current page location, title, and text content.

```
browser_evaluate(script: string)
```
Execute JavaScript in page context, returns result.

```
browser_wait_for(selector: string, timeout?: number)
```
Wait for element to appear (timeout in seconds).

### Advanced Features

```
browser_screenshot(options?: { fullPage?: boolean, path?: string })
```
Capture screenshot of current page.

```
browser_generate_pdf(options?: { path?: string })
```
Generate PDF from current page (Chromium only).

### Debugging

```
browser_network_requests()
```
Returns all network requests made by the page.

```
browser_console_messages()
```
Returns console logs from the page.

### Session Management

```
browser_close()
```
Close current session and reset environment.

## Procedure

### Basic Web Scraping

```
1. browser_navigate(url="https://example.com", waitUntil="networkidle0")
2. browser_snapshot()  # verify page loaded
3. browser_evaluate(script="document.querySelectorAll('.item').length")  # count items
4. browser_evaluate(script="Array.from(document.querySelectorAll('.item')).map(e => e.textContent)")  # extract data
5. browser_close()
```

### Form Submission

```
1. browser_navigate(url="https://example.com/form")
2. browser_fill(selector="#name", value="John Doe")
3. browser_fill(selector="#email", value="john@example.com")
4. browser_select_option(selector="#country", value="US")
5. browser_click(selector="#submit")
6. browser_wait_for(selector=".success", timeout=5)
7. browser_snapshot()  # verify success
8. browser_close()
```

### Anti-Detection

Obscura automatically applies:
- Per-session fingerprint randomization
- Tracker blocking
- Real V8 JavaScript execution
- CDP protocol (indistinguishable from real Chrome)

No additional configuration needed for basic anti-detection.

## Decision Rules

| Scenario | Action |
|----------|--------|
| Need to interact with web page | Use `browser_*` tools |
| Need desktop GUI automation | Use `computer-use` skill |
| Need to execute code in sandbox | Use `open-code` skill |
| Need simple HTTP request | Use REST API directly |
| Page requires JavaScript | Obscura handles automatically (V8 engine) |
| Anti-detection required | Obscura applies automatically |

## Pitfalls

- **Don't** use `browser_evaluate` for simple DOM queries - use `browser_snapshot` + parse
- **Don't** forget `browser_close()` - sessions persist until closed
- **Don't** assume elements exist - use `browser_wait_for` before interacting
- **Don't** use pixel coordinates - Obscura uses CSS selectors (more reliable)
- **Don't** ignore `waitUntil` parameter - `networkidle0` ensures all resources loaded

## Verification

- [ ] Obscura container running (`docker ps | grep obscura`)
- [ ] MCP server accessible (`curl http://localhost:3100/health`)
- [ ] Agent can call `browser_navigate` successfully
- [ ] Results logged to OpenRec (check correlation_id trace)
- [ ] No security bypass (approval required for external requests)

## Security

- Browser automation requires `browser_automation` capability in agent profile
- External requests logged to OpenRec for audit
- Approval gate for sensitive operations (configurable per org)
- No credential storage in Obscura sessions (ephemeral)

## Integration with Mesh

```typescript
// OpenOrchestrator plan step
{
  "goal": "Scrape product data from example.com",
  "required_skills": ["open-browser"],
  "approval_required": false
}

// OpenAgents dispatch
POST /v1/runs
{
  "profile": "browser-automation",
  "goal_id": "...",
  "parameters": {
    "url": "https://example.com/products",
    "selector": ".product-item"
  }
}

// OpenRec audit
{
  "type": "browser.automation.completed",
  "payload": {
    "url": "https://example.com/products",
    "items_scraped": 42,
    "duration_ms": 3500
  }
}
```

## Related Skills

- `computer-use` - Desktop GUI automation (non-web)
- `open-code` - Code execution in sandboxed worktrees
- `open-mcp-scaffold` - Add new MCP tools to mesh
