from scout_core.seniority import quote_found


def test_quote_must_really_be_in_the_posting():
    desc = "We hire new grads. Freshers (2025/2026 batch) are welcome to apply!"
    assert quote_found("Freshers (2025/2026 batch) are welcome to apply", "SDE", desc)
    assert quote_found("freshers 2025 2026 batch are WELCOME to apply", "SDE", desc)  # case/punctuation
    assert not quote_found("Freshers are welcome to apply", "SDE", desc)  # paraphrase
    assert not quote_found("new grads", "SDE", desc)  # too short to be evidence
    assert not quote_found("", "SDE", desc)


def test_fresher_quote_must_say_so():
    from scout_core.seniority import supports_fresher
    assert supports_fresher("Freshers (2025/2026 batch) are welcome to apply")
    assert supports_fresher("Open to new grads and final-year students")
    assert not supports_fresher("You will ship features with a small team")
    assert not supports_fresher("New grads with 2+ years of internship experience preferred")
    assert not supports_fresher("Entry-level role; 3 years of experience required")
