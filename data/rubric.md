# Labeling rubric

The pipeline, the verifier and the hand-checked gold sample all use these definitions, so results can be compared field by field.

| Field | Allowed values | Rule |
|---|---|---|
| `auth` | set of `oauth2`, `api_key`, `basic`, `other` | `oauth2` = an OAuth 2.0 flow is documented for third-party apps (authorization code or client credentials). `api_key` = a static secret the user creates in a dashboard: API key, personal access token, private-app token, bot token, server token. `basic` = HTTP Basic auth (username/password, or key-as-username). `other` = HMAC/request signing, JWT service accounts, session cookies, mTLS, SAML. List every method the docs offer for the public API. |
| `access` | `self_serve`, `paid_plan`, `approval`, `partner`, `no_api` | The gate a developer hits before getting **working credentials for basic use**. `self_serve` = credentials available alone on a free account, free tier, free developer sandbox or free trial. `paid_plan` = API only on a paid plan the developer can buy themselves (card checkout). `approval` = a review/application before credentials or production use (app review, developer registration approval, admin approval, waitlist). `partner` = contact sales, Enterprise-only (sales-led) tier, or a partnership agreement. `no_api` = no public network API (only a CLI/library or nothing). |
| `api_type` | `rest`, `graphql`, `both`, `none` | The documented public API style. Webhooks alone do not count. RPC-over-HTTP JSON APIs (e.g. Slack Web API, Telegram Bot API) count as `rest`. |
| `api_breadth` | `broad`, `moderate`, `narrow`, `none` | broad = dozens of resource groups or 100+ endpoints; moderate = a handful of resource groups; narrow = a few endpoints. |
| `official_mcp` | `yes`, `no` | A vendor-official MCP server (remote or local) is documented by the vendor or published under the vendor's GitHub org. Community servers do not count. |
| `verdict` | `build_now`, `build_with_gate`, `outreach`, `not_viable` | Derived: `build_now` = self_serve + public API. `build_with_gate` = public API but needs paid_plan or approval. `outreach` = partner gate. `not_viable` = no_api. |

Every field carries evidence: the docs URL and a short quote that supports it.
