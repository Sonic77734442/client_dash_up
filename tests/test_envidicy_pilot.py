"""Pure pilot-policy tests: no stores, identity services or production env."""
from collections.abc import Mapping
import json

import pytest

from app.services import envidicy_pilot as pilot


def test_absent_setting_is_unrestricted_but_explicit_list_is_immutable():
    assert pilot.load_pilot_subjects({}) is None
    subjects = pilot.load_pilot_subjects({pilot.PILOT_ENV: '["verified-subject-one", "verified-subject-two"]'})
    assert subjects == frozenset({"verified-subject-one", "verified-subject-two"})
    assert isinstance(subjects, frozenset)


@pytest.mark.parametrize("raw", [
    "", " ", "[]", "null", "true", "1", '"verified-subject"', "{}",
    '["subject",]', '["subject", "subject"]', '[""]', '[null]', '[true]', '[1]', '[{}]', '[[]]',
    '[NaN]', '[Infinity]', '["subject"] trailing', '["unclosed',
    json.dumps(["x" * (pilot.MAX_SUBJECT_CHARACTERS + 1)]),
    json.dumps([f"subject-{index}" for index in range(pilot.MAX_PILOT_SUBJECTS + 1)]),
])
def test_present_empty_malformed_duplicate_or_unbounded_setting_fails_closed(raw):
    with pytest.raises(pilot.PilotConfigurationError, match="^Envidicy ID pilot configuration is invalid$"):
        pilot.load_pilot_subjects({pilot.PILOT_ENV: raw})


@pytest.mark.parametrize("subject", [
    " subject", "subject ", "subject name", "subject\tname", "subject\nname", "subject\x00name",
    "subject\x1fname", "subject\x7fname", "subject\x85name", "subject\u00a0name", "subject\u200bname",
    "subject\u202ename", "subject\ud800name",
])
def test_configured_members_reject_whitespace_and_unicode_controls(subject):
    with pytest.raises(pilot.PilotConfigurationError):
        pilot.validate_pilot_subjects(json.dumps([subject]))


@pytest.mark.parametrize("raw", [None, [], {}, 123, b'["subject"]'])
def test_non_string_configuration_is_invalid(raw):
    with pytest.raises(pilot.PilotConfigurationError):
        pilot.validate_pilot_subjects(raw)


def test_count_subject_length_and_multibyte_configuration_boundaries():
    members = [f"subject-{index}" for index in range(pilot.MAX_PILOT_SUBJECTS)]
    assert len(pilot.validate_pilot_subjects(json.dumps(members))) == pilot.MAX_PILOT_SUBJECTS
    assert pilot.validate_pilot_subjects(json.dumps(["x" * pilot.MAX_SUBJECT_CHARACTERS]))
    raw = '["subject"]'
    assert pilot.validate_pilot_subjects(raw + " " * (pilot.MAX_CONFIGURATION_BYTES - len(raw))) == frozenset({"subject"})
    with pytest.raises(pilot.PilotConfigurationError):
        pilot.validate_pilot_subjects(raw + " " * (pilot.MAX_CONFIGURATION_BYTES - len(raw) + 1))
    # Fewer than 32K codepoints may still exceed the UTF-8 byte limit.
    multibyte = json.dumps(["界" * 400 + str(index) for index in range(30)], ensure_ascii=False)
    assert len(multibyte) < pilot.MAX_CONFIGURATION_BYTES < len(multibyte.encode("utf-8"))
    with pytest.raises(pilot.PilotConfigurationError):
        pilot.validate_pilot_subjects(multibyte)


def test_oversized_configuration_is_rejected_before_json_parsing(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Oversized JSON must not be parsed")
    monkeypatch.setattr(pilot.json, "loads", forbidden)
    with pytest.raises(pilot.PilotConfigurationError):
        pilot.validate_pilot_subjects("x" * (pilot.MAX_CONFIGURATION_BYTES + 1))


def test_disabled_id_does_not_read_or_validate_any_pilot_configuration():
    class Unreadable(Mapping):
        def __getitem__(self, _key):
            raise AssertionError("Disabled ID must not read configuration")
        def __iter__(self):
            raise AssertionError("Disabled ID must not enumerate configuration")
        def __len__(self):
            raise AssertionError("Disabled ID must not inspect configuration")

    assert pilot.load_pilot_subjects(Unreadable(), enabled=False) is None
    assert pilot.load_pilot_subjects({pilot.PILOT_ENV: "malformed"}, enabled=False) is None


def test_default_environment_is_read_fresh_without_caching(monkeypatch):
    monkeypatch.delenv(pilot.PILOT_ENV, raising=False)
    assert pilot.load_pilot_subjects() is None
    monkeypatch.setenv(pilot.PILOT_ENV, '["subject-one"]')
    assert pilot.load_pilot_subjects() == frozenset({"subject-one"})
    monkeypatch.setenv(pilot.PILOT_ENV, '["subject-two"]')
    assert pilot.load_pilot_subjects() == frozenset({"subject-two"})
    monkeypatch.setenv(pilot.PILOT_ENV, "")
    with pytest.raises(pilot.PilotConfigurationError):
        pilot.load_pilot_subjects()


def test_exact_issuer_and_subject_are_required_without_email_or_normalization():
    subjects = pilot.validate_pilot_subjects('["Verified-Subject", "é", "e\\u0301"]')
    for subject in subjects:
        assert pilot.require_pilot_subject(pilot.ISSUER, subject, subjects=subjects) is None
    for subject in ("verified-subject", "Verified-Subject ", "user@example.test", "other-subject"):
        with pytest.raises(pilot.PilotSubjectDenied):
            pilot.require_pilot_subject(pilot.ISSUER, subject, subjects=subjects)
    for issuer in (pilot.ISSUER + "/", pilot.ISSUER.upper(), "https://attacker.example/realms/envidicy", None):
        with pytest.raises(pilot.PilotSubjectDenied):
            pilot.require_pilot_subject(issuer, "Verified-Subject", subjects=subjects)


@pytest.mark.parametrize("subject", [None, 1, "", "x" * 513, "subject\x00", "subject\n"])
def test_unrestricted_mode_does_not_accept_invalid_identity(subject):
    with pytest.raises(pilot.PilotSubjectDenied):
        pilot.require_pilot_subject(pilot.ISSUER, subject, subjects=None)


def test_unrestricted_mode_preserves_existing_identity_key_subject_contract():
    for subject in ("subject", " subject with spaces ", "x" * 512, "é", "subject\x7f"):
        assert pilot.require_pilot_subject(pilot.ISSUER, subject, subjects=None) is None
    with pytest.raises(pilot.PilotSubjectDenied):
        pilot.require_pilot_subject("https://wrong.example", "subject", subjects=None)


def test_empty_explicit_set_denies_every_subject_instead_of_becoming_unrestricted():
    with pytest.raises(pilot.PilotSubjectDenied):
        pilot.require_pilot_subject(pilot.ISSUER, "subject", subjects=frozenset())


def test_error_messages_do_not_disclose_the_private_setting_or_rejected_subject():
    private = "private-test-subject-do-not-echo"
    with pytest.raises(pilot.PilotConfigurationError) as malformed:
        pilot.validate_pilot_subjects('["' + private + '"')
    assert private not in str(malformed.value)
    assert malformed.value.__suppress_context__
    assert not hasattr(malformed.value, "doc")
    assert malformed.value.code == "envidicy_pilot_configuration_invalid"
    with pytest.raises(pilot.PilotSubjectDenied) as denied:
        pilot.require_pilot_subject(pilot.ISSUER, private, subjects=frozenset({"another-subject"}))
    assert private not in str(denied.value)
    assert denied.value.code == "envidicy_pilot_subject_denied"
