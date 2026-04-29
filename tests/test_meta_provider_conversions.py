from app.services.providers import meta


def test_sum_actions_conversions_uses_default_types():
    actions = [
        {"action_type": "link_click", "value": "55"},
        {"action_type": "purchase", "value": "3"},
        {"action_type": "offsite_conversion.fb_pixel_lead", "value": "7"},
    ]
    value = meta._sum_actions_conversions(actions)
    assert value == 10.0


def test_sum_actions_conversions_returns_none_when_no_matches():
    actions = [
        {"action_type": "link_click", "value": "55"},
        {"action_type": "page_engagement", "value": "8"},
    ]
    assert meta._sum_actions_conversions(actions) is None

