from agent.fetch import Page
from agent.research import norm_value, verdict_for
from agent.score import compare
from agent.signals import extract, summarize, supports


def page(text, url="https://developers.example.com/docs/authentication"):
    return Page(url=url, status=200, final_url=url, text=text * 3)


def test_auth_rules_find_oauth_and_keys():
    s = summarize(extract(page("Our API supports OAuth 2.0 and personal access tokens. ")))
    assert s["auth"]["oauth2"] == 3
    assert s["auth"]["api_key"] == 3


def test_negated_claim_is_demoted():
    s = summarize(extract(page("All endpoints reject requests that use Basic Authentication. ")))
    assert s["auth"]["basic"] == 1


def test_changelog_pages_are_ignored():
    assert extract(page("OAuth 2.0 is now GA. ", url="https://example.com/changelog/oauth-ga")) == []


def test_paid_plan_rule():
    s = summarize(extract(page("Only stores on paid plans can access API. ")))
    assert s["access"]["paid_plan"] == 3


def test_grounding_requires_supporting_words():
    assert supports("auth", "oauth2", "uses the OAuth 2.0 authorization code flow")
    assert not supports("auth", "oauth2", "create an API key in settings")


def test_norm_and_verdict():
    assert norm_value("auth", "Bearer token") == "api_key"
    assert norm_value("access", "enterprise") == "partner"
    assert verdict_for("self_serve", "rest") == "build_now"
    assert verdict_for("approval", "rest") == "build_with_gate"
    assert verdict_for("no_api", "none") == "not_viable"


def test_score_skips_unconfirmable_fields():
    gold = {"auth": None, "access": "partner", "api_type": None, "official_mcp": "yes", "verdict": "outreach"}
    pred = {"auth": ["api_key"], "access": "partner", "api_type": "rest", "official_mcp": "no", "verdict": "outreach"}
    assert compare(pred, gold) == {"access": True, "official_mcp": False, "verdict": True}
