# Groq operator assistant

Open **Assistant** in the console. Choose network summary, recommendation explanation,
or incident summary. Select a record for the latter two. All authenticated roles can
request a briefing; this endpoint cannot approve, allocate, or change settings.

Local configuration (never commit keys):

```dotenv
GROQ_API_KEY=your-key
GROQ_MODEL=openai/gpt-oss-20b
LLM_TIMEOUT_SECONDS=12
```

Restart the backend after changing configuration. Docker Compose forwards these values.
The model runs on Groq; its `openai/` model-name prefix does not mean requests go to
OpenAI. Requests only go to `https://api.groq.com/openai/v1/chat/completions`.
Model selection follows Groq's strict structured-output support:
https://console.groq.com/docs/structured-outputs

Only the selected operational records are sent, not credentials or environment files.
One call per process at a time, at least two seconds between calls, 30,000-character
evidence limit, and a bounded response/timeout limit control usage. Free-tier limits
depend on the account; no unlimited-free or availability guarantee is assumed.

Missing configuration, unavailable data, rate limits, timeouts, malformed responses,
and invalid citation IDs produce a clearly labelled factual template. Snapshot tick,
run, stale status, and expandable source records accompany responses. An answer that
outlives its snapshot is flagged for refresh. The normal dashboard summary remains a
template so background dashboard use does not incur model calls.

Schema and citation validation do not prove semantic correctness. AI explanations
remain labelled for operator verification. No tools or execution credentials are
provided to the model. Automated tests mock provider success, failures, citation
errors, auth, and stale data; a live call is separate evidence.

Validation on 29 September 2026: full backend suite 60 passed; frontend suite
16 passed; backend/frontend lint and production build passed. After improving
provider error messages, all 10 assistant backend tests passed again.
Live Groq verification was attempted with synthetic data, but the connection
was reset during TLS negotiation (Windows error 10054). The factual fallback
worked; a successful live model response remains unverified. Browser visual
verification was unavailable because the browser tool failed to initialize.
