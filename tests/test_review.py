from agent.review import apply, queue


def _rec(id_, access="self_serve", pages=5, flags=None):
    return {"id": id_, "name": f"app{id_}", "pages_read": pages, "flags": flags or [],
            "final": {"auth": ["api_key"], "access": access, "api_type": "rest", "official_mcp": "no", "verdict": "build_now"}}


def test_zero_pages_is_queued_and_left_unverified():
    res = {"apps": [_rec(1, pages=0)]}
    assert [i["kind"] for i in queue(res)] == ["no_evidence"]
    apply(res, [])
    assert res["apps"][0]["reviewed"]["verdict"] == "unverified"
    assert res["apps"][0]["blocker_final"].startswith("unresolved")


def test_human_access_decision_lifts_the_evidence_floor():
    res = {"apps": [_rec(1, pages=0)]}
    apply(res, [{"id": 1, "field": "access", "value": "partner", "url": "u", "quote": "q", "note": "enterprise only"}])
    rv = res["apps"][0]["reviewed"]
    assert rv["verdict"] == "outreach"
    assert res["apps"][0]["blocker_final"] == "enterprise only"


def test_agent_answer_is_kept_next_to_the_review():
    res = {"apps": [_rec(1)]}
    apply(res, [{"id": 1, "field": "access", "value": "approval", "url": "u", "quote": "q"}])
    a = res["apps"][0]
    assert a["final"]["access"] == "self_serve" and a["reviewed"]["access"] == "approval"
    assert a["reviewed"]["verdict"] == "build_with_gate"
    assert a["blocker_final"] == "app review or access approval before production use"


def test_no_api_clears_auth_and_api_type():
    res = {"apps": [_rec(1)]}
    apply(res, [{"id": 1, "field": "access", "value": "no_api", "url": "u", "quote": "q"}])
    rv = res["apps"][0]["reviewed"]
    assert rv["auth"] == [] and rv["api_type"] == "none" and rv["verdict"] == "not_viable"
