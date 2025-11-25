"""Unit tests for two-phase query parser."""

from src.api.gpu.search_provider import parse_two_phase_query


def test_two_phase_format():
    """Test [SEARCH]/[TASK] format parsing."""
    query = "[SEARCH] auth patterns [TASK] Implement OAuth2 login"
    search, context = parse_two_phase_query(query)

    assert search == "auth patterns"
    assert "Implement OAuth2 login" in context
    assert "auth patterns" in context
    print("✓ test_two_phase_format passed")


def test_fallback_no_markers():
    """Test fallback when no markers present."""
    query = "authentication patterns"
    search, context = parse_two_phase_query(query)

    assert search == "authentication patterns"
    assert context == "authentication patterns"
    print("✓ test_fallback_no_markers passed")


def test_empty_search_phrase():
    """Test fallback when search phrase is empty."""
    query = "[SEARCH]   [TASK] Implement OAuth2"
    search, context = parse_two_phase_query(query)

    # Should fall back to full query
    assert search == query
    assert context == query
    print("✓ test_empty_search_phrase passed")


def test_empty_task():
    """Test fallback when task is empty."""
    query = "[SEARCH] auth patterns [TASK]  "
    search, context = parse_two_phase_query(query)

    # Should fall back to full query
    assert search == query
    assert context == query
    print("✓ test_empty_task passed")


def test_whitespace_handling():
    """Test that whitespace is properly stripped."""
    query = "[SEARCH]  auth patterns   [TASK]  Implement OAuth2 login  "
    search, context = parse_two_phase_query(query)

    assert search == "auth patterns"
    assert "Implement OAuth2 login" in context
    print("✓ test_whitespace_handling passed")


def test_task_with_search_keyword():
    """Test task containing the word SEARCH."""
    query = "[SEARCH] database patterns [TASK] SEARCH for user records in database"
    search, context = parse_two_phase_query(query)

    assert search == "database patterns"
    assert "SEARCH for user records in database" in context
    print("✓ test_task_with_search_keyword passed")


def test_multiline_task():
    """Test task with newlines (edge case)."""
    query = "[SEARCH] migration patterns [TASK] Migrate database\nwith zero downtime"
    search, context = parse_two_phase_query(query)

    assert search == "migration patterns"
    assert "Migrate database" in context
    assert "zero downtime" in context
    print("✓ test_multiline_task passed")


def test_only_search_marker():
    """Test fallback when only SEARCH marker present."""
    query = "[SEARCH] auth patterns"
    search, context = parse_two_phase_query(query)

    # Should fall back to full query
    assert search == query
    assert context == query
    print("✓ test_only_search_marker passed")


def test_only_task_marker():
    """Test fallback when only TASK marker present."""
    query = "[TASK] Implement OAuth2 login"
    search, context = parse_two_phase_query(query)

    # Should fall back to full query
    assert search == query
    assert context == query
    print("✓ test_only_task_marker passed")


def test_context_format():
    """Test that context format matches expected structure."""
    query = "[SEARCH] auth patterns [TASK] Implement OAuth2"
    search, context = parse_two_phase_query(query)

    # Check format: "{task}\n\nRelevant concepts: {search}"
    assert context.startswith("Implement OAuth2")
    assert "\n\n" in context
    assert context.endswith("auth patterns")
    assert "Relevant concepts:" in context
    print("✓ test_context_format passed")


if __name__ == "__main__":
    print("Running query parser tests...\n")

    test_two_phase_format()
    test_fallback_no_markers()
    test_empty_search_phrase()
    test_empty_task()
    test_whitespace_handling()
    test_task_with_search_keyword()
    test_multiline_task()
    test_only_search_marker()
    test_only_task_marker()
    test_context_format()

    print("\n✅ All tests passed!")
