"""Standalone unit tests for two-phase query parser."""


def parse_two_phase_query(query: str) -> tuple[str, str]:
    """
    Parse a two-phase query into (search_phrase, full_context).
    Copied from search_provider.py for standalone testing.
    """
    # Check for required markers
    if "[SEARCH]" not in query:
        raise ValueError(
            "Query format error: Missing [SEARCH] marker.\n"
            "Required format: [SEARCH] <short keyword phrase> [TASK] <task description>\n"
            f"Your query: {query[:200]}"
        )

    if "[TASK]" not in query:
        raise ValueError(
            "Query format error: Missing [TASK] marker.\n"
            "Required format: [SEARCH] <short keyword phrase> [TASK] <task description>\n"
            f"Your query: {query[:200]}"
        )

    # Parse [SEARCH]/[TASK] format
    parts = query.split("[TASK]", 1)
    search = parts[0].replace("[SEARCH]", "").strip()
    task = parts[1].strip()

    # Validate: both parts must be non-empty
    if not search:
        raise ValueError(
            "Query format error: [SEARCH] phrase is empty.\n"
            f"Your query: {query[:200]}"
        )

    if not task:
        raise ValueError(
            "Query format error: [TASK] context is empty.\n"
            f"Your query: {query[:200]}"
        )

    # Construct full context for reranking
    full_context = f"{task}\n\nRelevant concepts: {search}"
    return (search, full_context)


def test_two_phase_format():
    """Test [SEARCH]/[TASK] format parsing."""
    query = "[SEARCH] auth patterns [TASK] Implement OAuth2 login"
    search, context = parse_two_phase_query(query)

    assert search == "auth patterns"
    assert "Implement OAuth2 login" in context
    assert "auth patterns" in context
    print("✓ test_two_phase_format")


def test_missing_search_marker():
    """Test error when SEARCH marker is missing."""
    query = "auth patterns [TASK] Implement OAuth2"
    try:
        parse_two_phase_query(query)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Missing [SEARCH] marker" in str(e)
        assert "Required format" in str(e)
        print("✓ test_missing_search_marker")


def test_missing_task_marker():
    """Test error when TASK marker is missing."""
    query = "[SEARCH] auth patterns"
    try:
        parse_two_phase_query(query)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Missing [TASK] marker" in str(e)
        assert "Required format" in str(e)
        print("✓ test_missing_task_marker")


def test_empty_search_phrase():
    """Test error when search phrase is empty."""
    query = "[SEARCH]   [TASK] Implement OAuth2"
    try:
        parse_two_phase_query(query)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "[SEARCH] phrase is empty" in str(e)
        print("✓ test_empty_search_phrase")


def test_empty_task():
    """Test error when task is empty."""
    query = "[SEARCH] auth patterns [TASK]  "
    try:
        parse_two_phase_query(query)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "[TASK] context is empty" in str(e)
        print("✓ test_empty_task")


def test_whitespace_handling():
    """Test that whitespace is properly stripped."""
    query = "[SEARCH]  auth patterns   [TASK]  Implement OAuth2 login  "
    search, context = parse_two_phase_query(query)

    assert search == "auth patterns"
    assert "Implement OAuth2 login" in context
    print("✓ test_whitespace_handling")


def test_context_format():
    """Test that context format matches expected structure."""
    query = "[SEARCH] auth patterns [TASK] Implement OAuth2"
    search, context = parse_two_phase_query(query)

    assert context.startswith("Implement OAuth2")
    assert "\n\n" in context
    assert context.endswith("auth patterns")
    assert "Relevant concepts:" in context
    print("✓ test_context_format")


def test_error_message_helpfulness():
    """Test that error messages provide helpful guidance."""
    query = "just a plain query"
    try:
        parse_two_phase_query(query)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        error_msg = str(e)
        # Should include format requirement
        assert "[SEARCH]" in error_msg
        assert "[TASK]" in error_msg
        # Should show user's query
        assert "just a plain query" in error_msg
        print("✓ test_error_message_helpfulness")


if __name__ == "__main__":
    print("Running query parser tests...\n")

    test_two_phase_format()
    test_missing_search_marker()
    test_missing_task_marker()
    test_empty_search_phrase()
    test_empty_task()
    test_whitespace_handling()
    test_context_format()
    test_error_message_helpfulness()

    print("\n✅ All tests passed!")
