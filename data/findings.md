## 79 of 100 apps can be a toolkit today; the other 21 are blocked by people, not by technology
Only 2 apps (Sherlock, Mermaid CLI) have no network API at all. Everywhere else the API exists and is documented. What stops a build is a **human gate**: an app review (7), a sales conversation (6) or a paid plan (4). 2 more could not be settled from public docs. So the bottleneck for catalog growth is outreach and approvals, not engineering.

## The gate depends on who the vendor sells to, not on the category label
Developer tools, productivity and messaging are **10/10 self-serve** in each category. The gates cluster in three places: **ad and marketplace platforms** that guard other businesses' accounts (Google Ads developer token, LinkedIn Advertising API, Amazon SP-API, Pinterest: all approval), **sales-led data and finance tools** (PitchBook, DealCloud, Clay, Otter: enterprise contract), and **AI/SEO APIs sold by usage** (Ahrefs, SE Ranking, Devin, NotebookLM: paid plan).

## API keys are the floor, OAuth is the multiplier: support both
`api_key` appears in 75 apps and `oauth2` in 61, and **39 apps offer both**. 21 apps are OAuth-only, which means Composio has to register and maintain its own OAuth app for each (and often pass that vendor's app review). HTTP Basic (16) is usually an API key sent as the username (Freshdesk, Zendesk, Twilio). Request signing is rare: Binance (HMAC/RSA/Ed25519) is the clear case.

## The most common blocker is app review, and it usually only bites in production
7 apps put a review between the developer and production: Google Ads (developer token), LinkedIn Ads, Pinterest, Amazon SP-API, fanbasis, Brex, Ramp. Often the review only gates production or *other* businesses' accounts: a Google Ads token works on test accounts before review, and Meta Ads gives Limited access on your own ad accounts by default, with Full access after App Review. The playbook: **build on test access now, start the review in parallel**.

## 69 vendors already ship an official MCP server, including 9 of the gated apps
Official MCP is now common in every category (4 in marketing, up to 9 in dev tools and productivity). For 9 of the gated apps (DealCloud, Pylon, Clay, SE Ranking, Ahrefs, Brex, Ramp, Devin, Pinterest) the vendor already ships one, so once the gate is cleared **Composio can wrap the vendor's MCP** instead of writing every tool from scratch. There are also 691 community MCP servers across 71 apps, which are not counted as official.

## Easy wins: 12 apps Composio doesn't cover yet are buildable today with self-serve keys
Against Composio's public toolkit list (1,843 toolkits), 20 of the 100 apps have no toolkit. **12 of those are build_now**: LiveAgent, Gladly, Vonage, systeme.io, WooCommerce, Magento, Squarespace, Ecwid, Waterfall.io, Binance, iPayX, Reducto. 8 of them already have a vendor MCP to compare against. These go straight to toolkit engineering. The outreach list is short and specific: DealCloud, Pylon, Salesforce Commerce Cloud, Clay, PitchBook, Otter AI.
