# S19 — Genkit Provider Portability Proof

Depends on S07. Keep OpenAI as the explicit initial coordinator configuration and add pinned
Anthropic and Gemini Genkit adapters behind the same model factory. Do not use LiteLLM by default,
infer a provider from ambient credentials, or switch providers after failure. Run shared contracts
for multi-step tools, structured output with tools, streaming where used, continuation, reasoning
parameters, usage, errors, and skill-guided context. Record capability differences rather than
discarding settings. Deterministic tests are required; each live provider run is opt-in and bounded.
