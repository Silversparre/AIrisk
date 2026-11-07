# Claude-assisted assessment and review

AIrisk supports a human-controlled handoff to **Claude.ai or a Claude Enterprise workspace**. This works with web access today: no Anthropic API key is required. The application prepares context, validates the response brought back by the user, and lets an authorized person accept specific draft suggestions.

The workspace selector records which Claude environment the user intends to use. It does not authenticate an Enterprise account, send data automatically, or verify the origin of pasted text. The user selects their approved organization workspace in Claude before sharing the prompt.

## Drafting an assessment

1. Open a current draft assessment as its author, system owner or administrator, and select **Claude assistance**.
2. Choose **Claude.ai** or **Claude Enterprise**. Select the risks to consider; incomplete risks are selected by default. Include evidence notes/links only when appropriate to share them. Prepare the prompt.
3. Inspect the entire prompt, then copy it or download the text file. Open Claude, choose the intended workspace and paste or attach the prompt.
4. Copy Claude's complete JSON response into AIrisk. Optionally record the model name shown in Claude. Select **Preview suggestions**.
5. Compare current and proposed reasoning, ratings, control recommendations, evidence gaps and questions. Explicitly select the suggestions to use and confirm that you reviewed them.
6. Continue the assessment. Acceptance changes only the selected risk rationales and four ratings. Record implemented controls and evidence through the normal risk/evidence forms, then request independent review.

Unknown ratings can remain `null`. Numeric residual ratings must cite evidence actually included in the request; omitted, empty or uncited evidence requires `null` residual ratings. A citation does not attach or verify evidence automatically. A proposed control is not treated as an implemented control, and a model suggestion is not independent evidence. A risk marked not applicable needs human reconsideration in the assessment before a suggestion for it can be applied.

## Assisting an independent reviewer

An independent reviewer can prepare a critique while an assessment is in review. The response identifies weak reasoning, evidence gaps and questions. It remains advisory: there is no apply-to-draft control in this mode. The reviewer separately verifies evidence, records legal applicability and makes the decision or returns the assessment with requested changes.

## Integrity and provenance

Each request preserves the selected context, source mappings, prompt, preparer, workspace label and response. The model label is user-reported. Accepted risk IDs and timestamps are recorded in the audit history, JSON export and printable report.

A context fingerprint covers system/vendor context, assessment state/version, all risk revisions and evidence contents. Later changes invalidate an outstanding request, even if evidence was excluded from the shared prompt. Imported responses must match the request and selected risk IDs. Strict field, type, size, evidence-reference and rating validation prevents the response from assigning roles, verifying evidence or changing a decision. Imported text is escaped when rendered. All mutations require authentication, authorization and CSRF protection; imports and acceptance use optimistic concurrency control.

The prompt treats source/evidence text as untrusted content, asks Claude to preserve uncertainty and requests the exact `airisk.claude.v1` response shape. These instructions help guide the model; application permissions and validation enforce the allowed changes. The application cannot establish the truth of model-generated reasoning or confirm which service produced a pasted response.

## Enterprise integration path

The current feature supports Claude Enterprise through its web workspace. Direct application calls require separately configured Claude API access; Claude's web plans and Console/API access are distinct. See [Anthropic's API access guidance](https://support.claude.com/en/articles/9876003-why-is-claude-api-usage-billed-separately-from-my-paid-claude-plan).

A future Enterprise connector can expose bounded AIrisk tools through remote MCP: read permitted assessment context and submit advisory suggestions through the same validation boundary. It needs organization approval, authenticated user scopes, a reachable HTTPS endpoint and an end-to-end test in the actual Enterprise environment. Claude's remote connectors originate from Anthropic infrastructure, so the current localhost server is not such an endpoint. See [Anthropic's custom connector and network requirements](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).

The next integration increment should preserve the explicit acceptance step, independent review, context-change checks and provenance. Automatic approval or verification is outside its scope. API access, SSO and remote hosting should be configured against the organization's actual contract and permissions rather than inferred from the Enterprise label.

## Validation

The workflow tests cover permission checks, selective acceptance, forged identifiers, malformed or oversized fields, missing evidence, stale context, repeat submissions, non-applicability exclusions and escaped AI text. The Chromium test exercises both draft and review handoffs using **synthetic responses**, followed by the ordinary human decision flow. These checks validate AIrisk's handoff contract; they do not claim a live Claude model or Enterprise connector was tested.
