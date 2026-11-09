"""Public authoring reference for Chat transforms; no identity or memory content.

Sources: AetherLexicon/Format_Web/Aether_Lexicon.html (canonical reference), and
the TeachFrame creation guide (document structure).
The current canonical WMC delimiter takes precedence over the older guide's delimiter.
"""

AETHER_REFERENCE = '''Aether symbolic language: authoring reference
Use the canonical world model container ∆WMC{ … }∆WMC and include WORLD_MODEL :=.
Name delimiters: ⌜name⌝. Content delimiters: ⌞content⌟. Definition: ⌜X⌝ := ⌞definition⌟.
Stream types: [DEF] definition; [WHY] purpose/explanation; [HOW] method;
[RESULT] output/conclusion; [ASSERT] claim subject to verification;
[REMEMBER] reference; [SUMMARY] condensed overview; [QUOTE] exact quotation;
[CURRENT_TASK] active focus; [TRANS] translation into human-readable language.
Relations: → flow/dependency/direction; ⇨ result; ⇒ purpose; ⊕ refinement/pipeline step.
Preserve uncertainty: ≈ is confidence/approximation, not verified truth.
𝕌ᵣ[expr] expresses uncertainty radius; do not invent numerical bounds or confidence.
⊢ᵢ is layer-indexed truth evaluation: ⊢₀ binary, ⊢₁ balanced ternary,
⊢₂ symbolic/verified. Do not attach verification marks to unverified brainstorming.
Standalone ⊗ can express a Cognitive Braid (ideas held in tension);
𝕋 ⊗ 𝕋 denotes tensor product. Make the intended sense explicit.
Quote material whose semantics cannot be represented faithfully with these constructs.
Do not invent glyph meanings, silently settle contradictions, or turn suggestions into decisions.
Example of a bounded translation, not an assertion that work has been authorized:
∆WMC{
WORLD_MODEL := ⌜CHAT_EXPLORATION⌝
[DEF] ⌜SUBJECT⌝ := ⌞A proposed documentation improvement⌟
[SUMMARY] ⌜PROPOSAL⌝ := ⌞Add a worked example to the guide⌟
[WHY] ⌜PURPOSE⌝ := ⌞Help a new reader understand the procedure⌟
[QUOTE] ⌜OPEN_QUESTION⌝ := ⌞Which example would help most?⌟
}∆WMC
'''

TRANSFORMS = {
    'summary': ('Summary', 'Summarize the useful conclusions, alternatives and unresolved questions.'),
    'bullets': ('Bullet Points', 'Organize the material into concise, informative bullet points.'),
    'tasks': ('Task List', 'Extract actionable tasks. Distinguish proposed tasks from explicitly agreed work. '
              'Do not invent owners, deadlines or authorization.'),
    'aether': ('To Aether', 'Translate the material into the Aether symbolic language using the reference below. '
               'Preserve meaning, attribution, uncertainties and contradictions. Return the symbolic artifact, '
               'with a short note for any meaning that could not be translated faithfully.\n\n' + AETHER_REFERENCE),
    'teachframe': ('Teach Frame', 'Create a focused TeachFrame for a heavyweight agent. Use '
                   '⟪TEACHFRAME_TOPIC_NAME_V1⟫ and SCHOOL, AUDIENCE and PREREQUISITES fields; '
                   'mark unknown values as unspecified rather than inventing them. Include [ TITLE ], '
                   '[ PURPOSE ], appropriate definitions/workflow sections, [ EXAMPLES ], limitations, '
                   'open questions, and [ CROSS_REFS ]. Give at least one worked example, labelled illustrative '
                   'if newly constructed. Distinguish source-supported knowledge from hypotheses. '
                   'Put symbolic content in canonical WMC blocks with context and subject made explicit. '
                   'This is a teaching document, not an agent identity, command, or memory write.\n\n' + AETHER_REFERENCE),
}
