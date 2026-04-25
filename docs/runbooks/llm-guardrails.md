# LLM Guardrails

PolyPredictor does not use LLMs for numeric market estimates.

Allowed LLM uses:

- Classification assistance for ambiguous market wording, with human review.
- Plain-language narration of already-computed feature attributions.
- Summarization of supplied evidence snippets.

Forbidden LLM uses:

- Estimating probabilities, odds, prices, EV, edge, or Kelly sizing.
- Inventing feature values, source facts, or unsupported drivers.
- Replacing deterministic baselines, ensemble outputs, or conformal intervals.

Implementation boundary:

- Deterministic APIs may expose numbers directly.
- Any future external LLM narration must pass
  `api.llm_guardrails.validate_llm_narration`.
- The validator rejects numeric tokens, probability/odds/price/EV language, and
  driver claims not present in the supplied feature-label context.

Failure mode:

- If validation fails, discard the LLM text and fall back to deterministic
  driver summaries from served model attributions.
