# BDR Agent Oz Dev smoke checks
Use `scripts/bdr_oz_dev_smoke.py` to validate the non-production BDR Agent stage path in Oz Dev environment `example-oz-environment-id` before the production stage-completion webhook secret exists.
## Safety boundaries
- Do not enable HubSpot workflow `0000000000`.
- Do not perform live HubSpot writes.
- Do not print secret values; the Exa check reports only whether `BDR_AGENT_EXA_API_KEY` is present.
- Do not call the stage-completion webhook. Step 2 persistence smoke always runs with stage completion disabled.
- Writes are off by default. GCS write/read and Step 2 persistence require explicit allow flags plus `--test-id`.
- By default, Step 2 smoke skips live Exa calls. Add `--allow-live-exa` only for a scoped validation where using the BDR Exa key is approved.
- Do not modify source code. If the smoke script fails or a check reports an error, report the failure and stop. Do not create branches, push commits, or open pull requests. Source code fixes must be handled in a separate implementation task.
## Local or Oz Dev read-only smoke
Run from the repository root:
```bash
python scripts/bdr_oz_dev_smoke.py
```
This checks skill load paths, imports, `BDR_AGENT_EXA_API_KEY` boolean availability, and BigQuery read authentication in project `example-gcp-project`.
## Step 2 dry-run smoke
Use a test lead that is safe to read. Add contact/company fields when available; otherwise the runner will test BigQuery fallback. This does not write GCS or BigQuery rows and does not call stage-completion:
```bash
python scripts/bdr_oz_dev_smoke.py \
  --lead-id "$LEAD_ID" \
  --contact-id "${CONTACT_ID:-}" \
  --company-id "${COMPANY_ID:-}" \
  --company-domain "${COMPANY_DOMAIN:-}" \
  --trigger-source "bdr_oz_dev_smoke" \
  --source-system "oz_dev_smoke" \
  --hubspot-workflow-id "0000000000"
```
Use `--company-website` or `--company-alternative-domain` if those are the available company-backed domain sources.
By default the dry-run skips fresh live Exa research. If a scoped live Exa validation is approved, add `--allow-live-exa`.
## GCS write/read permission smoke
This writes and reads one JSON object under `gs://example-artifacts-bucket/bdr-agent/smoke/<test-id>/`:
```bash
python scripts/bdr_oz_dev_smoke.py \
  --test-id "$TEST_ID" \
  --allow-gcs-write-read
```
Use a unique test ID such as `ozdev-YYYYMMDD-initials-001`.
## Step 2 persistence smoke without stage completion
This writes the Step 2 JSON artifact and BigQuery rows, then explicitly skips stage-completion handoff. The smoke runner invokes the deterministic Step 2 code with the same safety behavior as the CLI `--skip-stage-completion` flag:
```bash
python scripts/bdr_oz_dev_smoke.py \
  --lead-id "$LEAD_ID" \
  --contact-id "${CONTACT_ID:-}" \
  --company-id "${COMPANY_ID:-}" \
  --company-domain "${COMPANY_DOMAIN:-}" \
  --test-id "$TEST_ID" \
  --allow-step2-persist
```
Expected output has `stage_completion.status` set to `skipped` with reason `disabled`.
## Running inside Oz Dev
If using Oz to run the smoke, target environment `example-oz-environment-id` and include the exact command to execute in the prompt. Whenever the tooling triggers or is run by an Oz cloud agent, report the Oz run link in the handoff so reviewers can inspect the transcript.
## Remaining production prerequisite
Production stage-completion chaining still depends on Terraform wiring the runtime webhook URL and secret. These smoke checks intentionally avoid that production dependency.
