# Qualification and Results

## Evidence precedence

Use lead identity evidence in this order:

1. Explicit structured fields supplied by OpenTeam.
2. TikTok author display name and business profile metadata.
3. Account handle, normalized by removing `@` and separators.
4. Caption or description text corroborated by profile data.

Do not derive a legal company name solely from a generic job title, hashtag, city, or
unrelated link. Preserve the original account and URL even when company identity differs.

A usable email must be syntactically valid and present in the supplied lead/profile data.
Do not infer common mailboxes such as `contact@domain`.

## Qualification

A qualified CRM lead has all of the following:

- a unique TikTok `video_url`;
- credible evidence that the post represents active recruitment or a hiring need;
- an identifiable company or recruiter organization;
- no duplicate returned by OpenCRM or OpenPro;
- a source-provided contact email.

Do not reject a lead for writing quality, follower count, company size, or an unfamiliar
industry. Do not infer protected or sensitive characteristics.

## Status mapping

| Status | Select when |
| --- | --- |
| `crm_created` | OpenCRM account/opportunity upsert returned success and outreach was not authorized. |
| `provisioned` | CRM upsert, OpenPro recruiter provisioning, and job creation all returned success. |
| `skipped_duplicate` | OpenCRM or OpenPro confirms the company/source already exists. |
| `skipped_no_email` | Identity is usable but no source-provided email is available. |
| `failed` | Validation, identity, authorization-dependent completion, tool, or callback processing failed. Include a sanitized error code/message. |

`processing` is transitional and must never be the final result.

When the trusted request is CRM-only, a successful CRM upsert is `crm_created`, not
`provisioned`.

## Safe error vocabulary

Prefer stable codes with a brief message:

- `invalid_lead`
- `missing_video_url`
- `identity_unresolved`
- `crm_unavailable`
- `crm_upsert_failed`
- `outreach_not_authorized`
- `openpro_unavailable`
- `provision_failed`
- `job_create_failed`
- `email_send_failed`
- `dm_send_failed`
- `callback_failed`
- `outcome_unknown`

Do not include response bodies that may contain credentials or personal data.

## Required batch result

Return one JSON-compatible object:

```json
{
  "correlation_id": "incoming correlation id",
  "input_count": 2,
  "unique_count": 2,
  "outreach_authorized": false,
  "totals": {
    "crm_created": 0,
    "provisioned": 0,
    "skipped_duplicate": 1,
    "skipped_no_email": 0,
    "failed": 1
  },
  "outcomes": [
    {
      "video_url": "https://www.tiktok.com/...",
      "company_name": "Example SAS",
      "status": "skipped_duplicate",
      "crm_account_id": null,
      "crm_opportunity_id": null,
      "openpro_recruiter_id": null,
      "openpro_post_id": null,
      "email_sent": false,
      "dm_sent": false,
      "error": null
    }
  ],
  "warnings": []
}
```

Use `null` for unavailable ids. Never synthesize ids. Include only sanitized error text.
