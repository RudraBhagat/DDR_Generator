# 3-5 Minute Loom Script

## 1. What I Built (40-60 sec)

I built an AI workflow that reads two technical documents, the Inspection Report and Thermal Report PDFs, and automatically generates a structured Main DDR report.

The system extracts observations, groups them area-wise, estimates root cause and severity with reasoning, recommends actions, and explicitly flags missing or unclear information with `Not Available`.

It also extracts relevant images from both source documents and includes them in the output report where mapping is possible.

## 2. How It Works (90-120 sec)

Pipeline steps:

1. Parse PDFs using block-level extraction.
2. Detect issue observations using moisture/leakage/crack/plumbing patterns.
3. Infer area from observation text and nearby context.
4. Deduplicate repeated points.
5. Extract rendered images from both PDFs.
6. Link images to areas using nearest text and page context fallback.
7. Parse thermal hotspot/coldspot values and compute thermal delta.
8. Generate required DDR sections:
   - Property Issue Summary
   - Area-wise Observations
   - Probable Root Cause
   - Severity Assessment (with reasoning)
   - Recommended Actions
   - Additional Notes
   - Missing or Unclear Information
9. Export final outputs to Markdown + JSON with evidence images.

## 3. Limitations (40-60 sec)

- Some source lines are layout-fragmented in the inspection PDF, so a few extracted observations can be partial.
- Thermal pages in this sample expose temperatures and image IDs clearly, but area labels are not consistently extractable, so area-level thermal mapping is explicitly marked as not available.
- Severity is rule-based, not trained on labeled historical outcomes.

## 4. What I Would Improve (40-60 sec)

- Add OCR + layout parser for better text reconstruction.
- Add semantic cross-document linking between inspection and thermal evidence.
- Add confidence scoring and human review checkpoints.
- Add template export to polished DOCX/PDF for direct client sharing.
- Add batch processing API for multiple properties.

## 5. Quick Demo Flow (30-40 sec)

Show:
1. Input PDFs in `input/`
2. Run command in terminal
3. Generated `output/DDR_Report.md`
4. Generated `output/DDR_Report.json`
5. Extracted evidence images under `output/images/`
6. Missing/conflict handling in report sections
