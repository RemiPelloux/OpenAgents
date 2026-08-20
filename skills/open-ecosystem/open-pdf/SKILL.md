---
name: open-pdf
description: PDF parsing and extraction via MCP
version: 1.0.0
metadata:
  tags: [pdf, parsing, extraction, document, mcp]
  category: document
  related_skills: [open-code]
---

# Open PDF (PDF Parsing MCP Integration)

Follow `open-ecosystem/OPERATING-STANCE.md`. Start the first real tool in this turn.

Extract text, tables, and metadata from PDF documents using the PDF parsing MCP server.

## When to Use

- Extract text from PDF documents
- Parse tables and structured data
- Extract metadata (author, creation date, etc.)
- Convert PDF to text for analysis
- Process invoices, reports, contracts

## When NOT to Use

- PDF generation (use browser PDF generation instead)
- Image extraction from PDFs (use specialized tools)
- PDF editing or modification
- Scanned PDFs without OCR capability

## Prerequisites

- PDF MCP Docker container running (`docker-compose.mesh.yml` includes `pdf-mcp`)
- MCP server accessible at `http://pdf-mcp:3200`
- Agent profile with `document_processing` capability

## Architecture

```
OpenOrchestrator (plan with PDF step)
  → OpenAgents (document-analyst profile)
  → MCP call to pdf-mcp-server
  → PDF container (parsing engine)
  → Results → OpenRec (audit) → OpenBrain (observation)
```

## PDF MCP Tools

### Basic Parsing

```
pdf_parse(file_path: string)
```
Extract all text content from PDF.

Returns:
```json
{
  "text": "Full text content...",
  "pages": 10,
  "metadata": {
    "title": "Document Title",
    "author": "Author Name",
    "creationDate": "2024-01-01"
  }
}
```

### Table Extraction

```
pdf_extract_tables(file_path: string, page?: number)
```
Extract tables from PDF (specific page or all pages).

Returns:
```json
{
  "tables": [
    {
      "page": 1,
      "headers": ["Column 1", "Column 2"],
      "rows": [
        ["Value 1", "Value 2"],
        ["Value 3", "Value 4"]
      ]
    }
  ]
}
```

### Metadata Extraction

```
pdf_get_metadata(file_path: string)
```
Extract PDF metadata only.

Returns:
```json
{
  "title": "Document Title",
  "author": "Author Name",
  "subject": "Document Subject",
  "creator": "Creator Application",
  "producer": "PDF Producer",
  "creationDate": "2024-01-01T00:00:00Z",
  "modificationDate": "2024-01-02T00:00:00Z",
  "pageCount": 10
}
```

### Page-Specific Extraction

```
pdf_extract_page(file_path: string, page_number: number)
```
Extract text from specific page.

## Procedure

### Basic Text Extraction

```
1. pdf_parse(file_path="/path/to/document.pdf")
2. Process extracted text
3. Store results in OpenBrain
```

### Table Processing

```
1. pdf_extract_tables(file_path="/path/to/invoice.pdf")
2. Parse table structure
3. Convert to structured data (JSON/CSV)
4. Validate extracted data
```

### Metadata Analysis

```
1. pdf_get_metadata(file_path="/path/to/document.pdf")
2. Extract document properties
3. Use for categorization or routing
```

## Decision Rules

| Scenario | Action |
|----------|--------|
| Need full text extraction | Use `pdf_parse` |
| Need structured table data | Use `pdf_extract_tables` |
| Need document properties | Use `pdf_get_metadata` |
| Need specific page only | Use `pdf_extract_page` |
| PDF is scanned/image-based | Flag for OCR tool (not supported) |
| PDF is encrypted/password-protected | Request password from user |

## Pitfalls

- **Don't** use for scanned PDFs without OCR
- **Don't** assume table extraction works for all formats
- **Don't** process very large PDFs (>100 pages) in single call
- **Don't** ignore metadata - it provides valuable context
- **Don't** forget to validate extracted data

## Verification

- [ ] PDF MCP container running (`docker ps | grep pdf-mcp`)
- [ ] Can extract text from sample PDF
- [ ] Can extract tables from structured PDF
- [ ] Metadata extraction works
- [ ] Results logged to OpenRec

## Security

- PDF parsing requires `document_processing` capability
- File paths must be within allowed directories
- No external network calls during parsing
- All operations logged to OpenRec

## Integration with Mesh

```typescript
// OpenOrchestrator plan step
{
  "goal": "Extract invoice data from PDF",
  "required_skills": ["open-pdf"],
  "approval_required": false
}

// OpenAgents dispatch
POST /v1/runs
{
  "profile": "document-analyst",
  "goal_id": "...",
  "parameters": {
    "file_path": "/data/invoices/INV-2024-001.pdf",
    "extract_tables": true
  }
}

// OpenRec audit
{
  "type": "pdf.parsing.completed",
  "payload": {
    "file": "INV-2024-001.pdf",
    "pages": 2,
    "tables_extracted": 1,
    "duration_ms": 450
  }
}
```

## Related Skills

- `open-code` - Code execution for post-processing
- `open-browser` - PDF generation from web pages
- `open-toolbox` - Discover additional document tools
