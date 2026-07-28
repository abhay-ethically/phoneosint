"""Unit tests for PhoneOsint's pure functions (no network calls).

Run with:
    pip install -r requirements-dev.txt
    pytest
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import phoneosint as po


# --------------------------------------------------------------------------- 
# normalize()
# ---------------------------------------------------------------------------

def test_normalize_valid_number_with_region():
    parsed = po.normalize("7410410123", "IN")
    assert parsed is not None
    assert po.phonenumbers.is_valid_number(parsed)


def test_normalize_lowercase_region_code():
    """Regression test: lowercase region codes used to raise internally and
    return None even for a valid number."""
    parsed = po.normalize("7410410123", "in")
    assert parsed is not None


def test_normalize_whitespace_is_stripped():
    parsed = po.normalize("  7410410123  ", " IN ")
    assert parsed is not None


def test_normalize_e164_number_ignores_region():
    parsed = po.normalize("+14155552671", "IN")
    assert parsed is not None
    assert parsed.country_code == 1


def test_normalize_invalid_number_returns_none():
    assert po.normalize("123", "IN") is None


def test_normalize_garbage_region_falls_back_gracefully():
    # An unparseable/unknown region should not crash; it should just fail
    # to produce a valid parse (garbage-in, None-out).
    assert po.normalize("7410410123", "ZZ") is None


def test_normalize_numeric_calling_code_as_region_is_resolved():
    """Regression test: a user prompted for a 'country code' may type the
    numeric calling code (e.g. '91') instead of the ISO region code
    ('IN'). This used to fail outright."""
    parsed = po.normalize("7410410123", "91")
    assert parsed is not None
    assert parsed.country_code == 91


def test_normalize_number_already_includes_calling_code_without_plus():
    """Regression test: a user may type the full number including the
    country calling code but without a leading '+' (e.g. '917410410123'
    with region 'IN' or '91'). This used to fail because it was parsed as
    an (invalid, too-long) national number instead of retried as
    international."""
    parsed = po.normalize("917410410123", "91")
    assert parsed is not None
    assert parsed.country_code == 91
    assert parsed.national_number == 7410410123

    parsed2 = po.normalize("917410410123", "IN")
    assert parsed2 is not None
    assert parsed2.country_code == 91


def test_normalize_does_not_false_positive_unrelated_calling_code():
    """The '+' retry must only fire when the number's leading digits match
    the resolved region's own calling code -- not blindly for any garbage
    digit string, to avoid coincidentally validating against an unrelated
    country."""
    assert po.normalize("123", "ZZ") is None


# --------------------------------------------------------------------------- 
# carrier_gateways()
# ---------------------------------------------------------------------------

def test_carrier_gateways_us_number_populates_gateways():
    result = po.carrier_gateways("14155552671", "US")
    assert "AT&T" in result
    assert result["AT&T"] == "4155552671@txt.att.net"


def test_carrier_gateways_non_us_ten_digit_number_no_false_positive():
    """Regression test: a non-US number whose digit count happens to be 10
    should NOT get bogus US carrier gateways attached."""
    result = po.carrier_gateways("7410410123", "IN")
    assert "AT&T" not in result
    assert "note" in result


def test_carrier_gateways_always_has_note():
    result = po.carrier_gateways("7410410123", "IN")
    assert "note" in result


# --------------------------------------------------------------------------- 
# basic_info()
# ---------------------------------------------------------------------------

def test_basic_info_fields_present_and_correct_types():
    parsed = po.normalize("7410410123", "IN")
    info = po.basic_info(parsed)
    expected_keys = {
        "e164", "national", "international", "country_code", "national_number",
        "region", "timezone", "carrier", "line_type", "possible", "valid",
    }
    assert expected_keys.issubset(info.keys())
    assert info["e164"] == "+917410410123"
    assert info["country_code"] == 91
    assert isinstance(info["timezone"], list)
    assert info["valid"] is True


# --------------------------------------------------------------------------- 
# generate_dorks() / direct_links() / search_engine_dorks()
# ---------------------------------------------------------------------------

def test_generate_dorks_returns_google_urls_for_all_queries():
    dorks = po.generate_dorks("+917410410123")
    assert "General" in dorks
    assert "WhatsApp" in dorks
    for url in dorks.values():
        assert url.startswith("https://www.google.com/search?q=")


def test_direct_links_structure():
    links = po.direct_links("+917410410123")
    assert links["WhatsApp"] == "https://wa.me/917410410123"
    assert links["Telegram"] == "https://t.me/917410410123"
    assert links["Viber"].startswith("viber://")


def test_search_engine_dorks_covers_multiple_engines():
    result = po.search_engine_dorks("+917410410123")
    assert "Google" in result
    assert "Bing" in result
    assert "DuckDuckGo" in result
    for engine_queries in result.values():
        assert "General" in engine_queries


# --------------------------------------------------------------------------- 
# aadhaar_linkage_note() / paypal_name_leak_note() -- informational only
# ---------------------------------------------------------------------------

def test_aadhaar_linkage_note_is_informational_only():
    result = po.aadhaar_linkage_note("+917410410123")
    assert result["lookup_possible"] is False
    assert "authorized_channel" in result


def test_paypal_name_leak_note_is_informational_only():
    result = po.paypal_name_leak_note("+917410410123")
    assert result["lookup_possible"] is False
    assert "why_not_automated" in result


def test_gravatar_lookup_without_email_returns_note():
    result = po.gravatar_lookup(None)
    assert "note" in result


def test_extra_phone_apis_without_keys_returns_note():
    result = po.extra_phone_apis("+917410410123", None, None, None)
    assert "note" in result


def test_business_directory_dorks_structure():
    result = po.business_directory_dorks("+917410410123")
    assert "Google_Maps" in result
    assert "JustDial" in result
    assert "note" in result


# --------------------------------------------------------------------------- 
# exposure_score()
# ---------------------------------------------------------------------------

def test_exposure_score_no_signals_is_low():
    report = {}
    score = po.exposure_score(report)
    assert score["score"] == 0
    assert score["level"] == "Low"


def test_exposure_score_telegram_and_truecaller_signals():
    report = {
        "telegram": {"registered": True},
        "truecaller": {"name": "Jane Doe"},
    }
    score = po.exposure_score(report)
    assert score["score"] == 40
    assert score["level"] == "Medium"
    assert len(score["reasons"]) == 2


def test_exposure_score_all_signals_present():
    report = {
        "telegram": {"registered": True},
        "truecaller": {"name": "Jane Doe"},
        "breach_lookup": {"psbdmp": [{"id": "abc"}]},
        "extra_apis": {"veriphone": {"valid": True}},
        "ignorant": {"registered_on": ["instagram.com"]},
        "dark_web": {"ahmia_live_status": 200},
    }
    score = po.exposure_score(report)
    # 20 (telegram) + 20 (truecaller) + 25 (breach) + 10 (extra_apis) + 15 (ignorant) + 10 (dark_web) = 100
    assert score["score"] == 100
    assert score["level"] == "High"
    assert len(score["reasons"]) == 6


def test_exposure_score_ignorant_signal():
    report = {"ignorant": {"registered_on": ["instagram.com", "amazon.com"]}}
    score = po.exposure_score(report)
    assert score["score"] == 15
    assert any("instagram.com" in r and "amazon.com" in r for r in score["reasons"])


def test_exposure_score_ignorant_no_accounts_found():
    report = {"ignorant": {"registered_on": []}}
    score = po.exposure_score(report)
    assert score["score"] == 0


# --------------------------------------------------------------------------- 
# _summarize_ignorant_results()
# ---------------------------------------------------------------------------

def test_summarize_ignorant_results_structure():
    raw_results = [
        {"name": "instagram", "domain": "instagram.com", "exists": True, "rateLimit": False},
        {"name": "snapchat", "domain": "snapchat.com", "exists": False, "rateLimit": True},
        {"name": "amazon", "domain": "amazon.com", "exists": False, "rateLimit": False},
    ]
    summary = po._summarize_ignorant_results(raw_results)
    assert summary["checked_sites"] == 3
    assert summary["registered_on"] == ["instagram.com"]
    assert summary["rate_limited_sites"] == ["snapchat.com"]
    assert summary["results"] == raw_results


def test_ignorant_lookup_missing_dependency(monkeypatch):
    """If ignorant somehow isn't importable, the function should degrade
    gracefully instead of raising."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ignorant.core":
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = po.ignorant_lookup(91, 7410410123)
    assert "note" in result


# --------------------------------------------------------------------------- 
# diff_reports()
# ---------------------------------------------------------------------------

def test_diff_reports_detects_new_and_removed_sections():
    previous = {"target": "+917410410123", "dorks": {"a": "1"}}
    current = {"target": "+917410410123", "telegram": {"registered": True}}
    diff = po.diff_reports(previous, current)
    assert any("New section added: 'telegram'" in c for c in diff["changes"])
    assert any("Section removed: 'dorks'" in c for c in diff["changes"])


def test_diff_reports_detects_value_change():
    previous = {"telegram": {"registered": False}}
    current = {"telegram": {"registered": True}}
    diff = po.diff_reports(previous, current)
    assert any("registered False -> True" in c for c in diff["changes"])


def test_diff_reports_no_changes():
    """diff_reports() itself returns an empty changes list when nothing
    differs; distinguishing this from 'no previous scan found' is the
    caller's responsibility (build_report sets previous_scan_time in that
    case -- see the regression test for print_report's display logic)."""
    report = {"target": "+917410410123", "dorks": {"a": "1"}}
    diff = po.diff_reports(report, dict(report))
    assert diff["changes"] == []


# ---------------------------------------------------------------------------
# local_file_search() / _cell_digits() -- generic user-supplied file search
# ---------------------------------------------------------------------------

def test_cell_digits_handles_float_mobile_without_trailing_zero():
    """Regression: openpyxl often reads phone numbers as floats
    (7410410123.0); naive str() conversion would leak a bogus trailing
    '0' digit and break matching."""
    assert po._cell_digits(7410410123.0) == "7410410123"
    assert po._cell_digits("+91 74104-10123") == "917410410123"
    assert po._cell_digits(None) == ""


def test_local_file_search_no_files_returns_note():
    result = po.local_file_search("+917410410123", 7410410123, [])
    assert "note" in result
    assert "files_searched" not in result


def test_local_file_search_missing_file_reports_error():
    result = po.local_file_search("+917410410123", 7410410123, ["/tmp/definitely_missing_phoneosint.csv"])
    assert result["total_matches"] == 0
    assert result["files_searched"][0]["error"] == "File not found."


def test_local_file_search_csv_match(tmp_path):
    csv_file = tmp_path / "authorized_test.csv"
    csv_file.write_text(
        "name,mobile,note\n"
        "Test Person,+917410410123,synthetic test row\n"
        "Unrelated,9999999999,no match\n"
    )
    result = po.local_file_search("+917410410123", 7410410123, [str(csv_file)])
    assert result["total_matches"] == 1
    assert result["matches"][0]["row"]["name"] == "Test Person"
    assert result["files_searched"][0]["rows_scanned"] == 2


def test_local_file_search_csv_no_match(tmp_path):
    csv_file = tmp_path / "authorized_test.csv"
    csv_file.write_text("name,mobile\nSomeone,1112223333\n")
    result = po.local_file_search("+917410410123", 7410410123, [str(csv_file)])
    assert result["total_matches"] == 0


# ---------------------------------------------------------------------------
# _safe_call()
# ---------------------------------------------------------------------------

def test_safe_call_returns_normal_result():
    assert po._safe_call(po.direct_links, "+917410410123") == po.direct_links("+917410410123")


def test_safe_call_captures_exception_instead_of_raising():
    def _boom(x):
        raise ValueError("kaboom")

    result = po._safe_call(_boom, 1)
    assert "error" in result
    assert "kaboom" in result["error"]


# ---------------------------------------------------------------------------
# _prompt_stderr()
# ---------------------------------------------------------------------------

def test_prompt_stderr_handles_eof_gracefully(monkeypatch):
    """Regression test: if stdin is exhausted/unavailable (e.g. redirected
    from /dev/null in a headless run), _prompt_stderr() used to let the raw
    EOFError propagate up into the report as a leaked error message. It
    should instead return an empty string so callers treat it as 'skip'."""
    def fake_input():
        raise EOFError()

    monkeypatch.setattr("builtins.input", fake_input)
    assert po._prompt_stderr("Some prompt: ") == ""


def test_diff_reports_ignores_volatile_error_fields():
    """Regression test: network exception text embeds non-deterministic
    object memory addresses; this should not be treated as a real change."""
    previous = {
        "breach_lookup": {
            "psbdmp_error": "ConnectionError at 0x1111aaa",
            "dehashed_search": "https://dehashed.com/search?query=x",
        }
    }
    current = {
        "breach_lookup": {
            "psbdmp_error": "ConnectionError at 0x2222bbb",
            "dehashed_search": "https://dehashed.com/search?query=x",
        }
    }
    diff = po.diff_reports(previous, current)
    assert diff["changes"] == []
