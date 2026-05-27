# Vertex Migration Eval

Scores the Gemini (Vertex) candidate against the Claude baseline per call-site,
using the acceptance thresholds in
`docs/EMAIL-MATCHER-VERTEX-MIGRATION-PLAN.md` (section 6.3).

## Setup

Runs in an environment with **both** providers configured (staging):

- `ANTHROPIC_API_KEY` (baseline)
- `GOOGLE_CLOUD_PROJECT` + `VERTEX_AI_REGION` + ADC (`GOOGLE_APPLICATION_CREDENTIALS`)
- both SDKs installed: `pip install -r requirements.txt`

## Fixtures

Pull ~100 real replies from prod into `eval/fixtures/email_replies.json`:

```sql
SELECT id, subject, cleaned_body AS body, from_email
FROM incoming_email
WHERE created_at > now() - interval '60 days'
ORDER BY created_at DESC
LIMIT 100;
```

Cover every intent class + settlement offers + with/without attachments.
**Do not anonymize** (names/amounts must be real for a meaningful extraction
test) and **do not commit** — the file is gitignored. Schema: see
`email_replies.sample.json`.

## Run

```bash
python eval/run_eval.py                          # all text call-sites
python eval/run_eval.py --call-sites settlement  # money-critical only
```

Exit code 0 = all call-sites at/above threshold (greenlight), 1 = below.

## Not covered

Vision call-sites (`pdf_extractor`, `image_extractor`) need binary attachment
fixtures and are evaluated separately (plan 6.3: >=90% token match on 50
test PDFs / 50 test images). Plus manual QA of 10 settlement replies (plan 6.4).
