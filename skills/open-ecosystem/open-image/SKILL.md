---
name: open-image
description: Image generation and manipulation via MCP
version: 1.0.0
metadata:
  tags: [image, generation, ai, creative, mcp]
  category: creative
  related_skills: [open-creative, open-code]
---

# Open Image (Image Generation MCP Integration)

Generate and manipulate images using AI-powered image generation MCP server.

## When to Use

- Generate images from text descriptions
- Create visual content for presentations
- Generate mockups and prototypes
- Create marketing materials
- Generate test images for development

## When NOT to Use

- Photo editing or retouching
- Vector graphics creation
- 3D modeling
- Video generation
- Real-time image processing

## Prerequisites

- Image generation MCP Docker container running (`docker-compose.mesh.yml` includes `image-mcp`)
- MCP server accessible at `http://image-mcp:3300`
- API key configured (if required by provider)
- Agent profile with `creative_generation` capability

## Architecture

```
OpenOrchestrator (plan with image generation)
  → OpenAgents (creative profile)
  → MCP call to image-mcp-server
  → Image container (generation engine)
  → Results → OpenRec (audit) → OpenBrain (observation)
```

## Image MCP Tools

### Text-to-Image Generation

```
image_generate(prompt: string, options?: {
  width?: number,
  height?: number,
  style?: string,
  quality?: 'standard' | 'high'
})
```

Generate image from text description.

Returns:
```json
{
  "image_url": "http://image-mcp:3300/images/generated-123.png",
  "width": 1024,
  "height": 1024,
  "format": "png",
  "prompt_used": "A beautiful sunset over mountains"
}
```

### Image Variation

```
image_variate(image_url: string, prompt?: string, variations?: number)
```

Create variations of existing image.

Returns:
```json
{
  "variations": [
    {
      "image_url": "http://image-mcp:3300/images/var-1.png",
      "similarity": 0.85
    }
  ]
}
```

### Image Editing

```
image_edit(image_url: string, edit_prompt: string, mask?: string)
```

Edit image based on text instructions.

Returns:
```json
{
  "image_url": "http://image-mcp:3300/images/edited-123.png",
  "original_url": "http://image-mcp:3300/images/original.png",
  "edit_applied": "Add a red hat to the person"
}
```

### Image to Text

```
image_describe(image_url: string)
```

Generate text description of image.

Returns:
```json
{
  "description": "A person standing on a mountain at sunset",
  "tags": ["person", "mountain", "sunset", "nature"],
  "confidence": 0.92
}
```

## Procedure

### Basic Image Generation

```
1. image_generate(prompt="A professional headshot of a software developer", width=512, height=512)
2. Download generated image
3. Store in asset library
4. Log to OpenRec
```

### Image Variation Workflow

```
1. image_generate(prompt="Company logo concept")
2. image_variate(image_url=result.image_url, variations=5)
3. Review variations
4. Select best option
5. Use in final design
```

### Image Editing

```
1. Load existing image
2. image_edit(image_url=url, edit_prompt="Make the background blue")
3. Verify edited result
4. Save final version
```

## Decision Rules

| Scenario | Action |
|----------|--------|
| Need new image from scratch | Use `image_generate` |
| Need variations of existing image | Use `image_variate` |
| Need to modify existing image | Use `image_edit` |
| Need to understand image content | Use `image_describe` |
| Need high-quality output | Set `quality: 'high'` |
| Need specific dimensions | Set `width` and `height` |

## Pitfalls

- **Don't** generate images for inappropriate content
- **Don't** assume generated images are copyright-free
- **Don't** use for photorealistic human faces (ethical concerns)
- **Don't** generate very large images (>2048px) without testing
- **Don't** forget to specify dimensions if needed
- **Don't** use generated images without review

## Verification

- [ ] Image MCP container running (`docker ps | grep image-mcp`)
- [ ] Can generate images from text prompts
- [ ] Can create variations
- [ ] Can edit existing images
- [ ] Generated images are valid format
- [ ] Results logged to OpenRec

## Security

- Image generation requires `creative_generation` capability
- API keys stored securely (not in prompts)
- Generated images stored in isolated directory
- All operations logged to OpenRec
- Content filtering enabled

## Cost Considerations

- Image generation may incur API costs
- Higher quality = higher cost
- Batch generation for efficiency
- Monitor usage via OpenRec

## Integration with Mesh

```typescript
// OpenOrchestrator plan step
{
  "goal": "Generate marketing banner image",
  "required_skills": ["open-image"],
  "approval_required": false
}

// OpenAgents dispatch
POST /v1/runs
{
  "profile": "creative",
  "goal_id": "...",
  "parameters": {
    "prompt": "Modern tech company banner with blue gradient",
    "width": 1200,
    "height": 400,
    "quality": "high"
  }
}

// OpenRec audit
{
  "type": "image.generation.completed",
  "payload": {
    "prompt": "Modern tech company banner...",
    "dimensions": "1200x400",
    "format": "png",
    "duration_ms": 3500
  }
}
```

## Related Skills

- `open-creative` - General creative content generation
- `open-code` - Image processing and manipulation
- `open-browser` - Screenshot capture
- `open-toolbox` - Discover additional image tools
