from hami_github_activity.models import is_bot, is_maintainer


def test_bot_detection_uses_type_suffix_and_known_accounts() -> None:
    assert is_bot({"login": "service", "type": "Bot"}) is True
    assert is_bot({"login": "custom[bot]", "type": "User"}) is True
    assert is_bot({"login": "renovate", "type": "User"}) is True
    assert is_bot({"login": "alice", "type": "User"}) is False


def test_maintainer_associations() -> None:
    assert is_maintainer("OWNER") is True
    assert is_maintainer("MEMBER") is True
    assert is_maintainer("COLLABORATOR") is True
    assert is_maintainer("CONTRIBUTOR") is False
