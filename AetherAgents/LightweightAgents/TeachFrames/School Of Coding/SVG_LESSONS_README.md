# SVG lessons for the three specialists

Michel authorized these lessons for SVGAnimator, SVGBackgroundArtist and CatAnimator on 2026-11-03. They are available through the existing `teachframe_list` → `briefcase_create` flow. The lead selects the relevant bundle; staff receive lesson cards in their ordinary wake and can `read_file` the full selected lessons. This does not automatically inject lessons into existing sprints.

| Agent and task | Exact catalog names to select |
|---|---|
| CatAnimator, cat body and gait | `SVG_ATTACHMENT`, `SVG_CAT_BODY_CONSTRUCTION` |
| SVGAnimator, cat face on the body agent's head | `SVG_ATTACHMENT`, `SVG_CAT_FACE_DETAILS` |
| CatAnimator or SVGAnimator, crossing cat | Above plus `SVG_FIGURE_CROSSING` |
| CatAnimator or SVGAnimator, centered cat with scrolling fence | Cat bundle plus `SVG_BACKGROUND_SCROLL` |
| SVGBackgroundArtist, background only | `SVG_ATTACHMENT`, `SVG_BACKGROUND_DESIGN` |
| SVGBackgroundArtist, scrolling background | Background bundle plus `SVG_BACKGROUND_SCROLL` |
| SVGAnimator, complete scene | Shared checks plus only the character, travel and scenery modules needed |

For example, after hiring `catanimator` in a new sprint and confirming the names with `teachframe_list`:

```json
{"agent_id":"catanimator","teachframes":["SVG_ATTACHMENT","SVG_CAT_BODY_CONSTRUCTION","SVG_FIGURE_CROSSING"]}
```

The existing tool is `briefcase_create`; this example is optional lesson selection, not a new workflow or automatic staffing policy. Never rewrite stopped runs or another agent's identity to deliver lessons. The specialists' existing nested-transform construction rules remain usable; the lessons explain valid alternatives without requiring them.

`CURRICULUM` here means selectable teaching material. These remain pilot guidance: their format, source examples and sampled renders were checked, but improved model generation has not been demonstrated. Use the lesson checks to inspect work and retain evidence of what succeeds or fails. Do not treat loading a lesson as certification.

[Provenance](SVG_LESSONS_PROVENANCE.json) records authorship, hashes, availability adaptations and the retained comparison path. The original guides, SVGs, prior lesson versions, graders and results remain in `AetherPlanAndAnalysis/CaseStudies/SVG_Teachframe_Pilot/`. The runtime needs only the selected lesson text; original samples are evidence, not execution dependencies.

The first live SVG Cat Polish baseline stopped on an invalid XML comment and an unlinked command retry. Michel authorized SVG_ATTACHMENT v1.5 and SVG_CAT_RIG v1.1: parse actual files early, use valid comments, and follow the existing bounded recovery protocol. Prior product bytes and the failure remain preserved in `AetherSprints/Sprint_Active/SVG_CAT_REPAIR_20261103/teachframes-before` and the baseline packet. This update does not claim improved generation or change runtime policy.

The repair trial produced a valid integrated SVG with sampled browser evidence, but stopped during participant review after a later omitted retry flag. Earlier bounded retries succeeded; reliable end-to-end recovery and memory closure remain unproven. See `AetherSprints/Sprint_Active/SVG_CAT_REPAIR_20261103/OPERATOR_REVIEW.txt`. The tested v1.5/v1.1 lesson bytes remain unchanged.

Guidance revision SVG_ATTACHMENT1.6 / SVG_CAT_RIG1.2 follows a trace audit of both stops. The generated shared card now calls out the top-level Boolean; the detailed lesson distinguishes pending failures, final exit status and error layers. For a new SVG review/closure follow-up, include `SVG_RECOVERY_GUIDANCE_20261103/RECOVERY_CHECKPOINT_DIRECTIVE.txt` verbatim via the existing `briefcase_create` directives list for each specialist. Preserve old briefs; never retrofit stopped runs. This revision has offline protocol/delivery validation only, not a new hosted-model efficacy result.

2026-11-04: SVG_ATTACHMENT1.7 / SVG_CAT_RIG1.3 incorporate the observed hollow-tail regression and incomplete cross-wake reporting. The authorized tail repair uses refreshed copies and a full evidence checkpoint in each existing TASK/CLOSURE wake. Prior editions preserved in SVG_TAIL_FILL_REPAIR_20261104/teachframes-before. No loop change or permanent refresh service. Future updates follow new observed evidence; loading does not prove adherence.

2026-11-04 post-run: current SVG_ATTACHMENT1.10 / SVG_CAT_RIG1.4 distill the successful six-path solid-tail repair, command-path and raw-byte measurement errors, and actual wake-delivery limits. Eight rendered tail crops equal the solid baseline. Four native owners closed. Prior editions and transcript receipts are in SVG_TAIL_FILL_REPAIR_20261104. These post-run editions await the next authorized wake; they were not retroactively delivered to finished owners.

2026-11-04 eyes-forward round: SVG_CAT_RIG1.5 adds fidelity to the user-selected face reference and matching closed-lid placement. Attachment1.10 and CatRig1.5 cards plus the current checkpoint reached all six staff task/closure wakes. Four eye/lid transforms were verified; nine open/blink/closed samples inspected after all four native owner closures. See SVG_EYES_FORWARD_20261104/OPERATOR_REVIEW.txt; no general teaching-efficacy claim.

2026-11-05: Michel retired SVG_CAT_RIG. Its rule against fixed proportions contradicts the construction frames SVG_CAT_BODY_CONSTRUCTION and SVG_CAT_FACE_DETAILS, which gave a passable cat on the first attempt in two trials (`SVG_TAUGHT_CAT_20261105`); Michel reviewed and accepted the second. The rig is archived unchanged at v1.5 in `TeachFrames/Retired Lessons/Archived SVG-01/`. SVG_ATTACHMENT1.11, SVG_BACKGROUND_SCROLL1.1 and SVG_FIGURE_CROSSING1.1 now route cats to the construction frames; prior bytes in `SVG_CAT_RIG_RETIREMENT_20261105/before`.
