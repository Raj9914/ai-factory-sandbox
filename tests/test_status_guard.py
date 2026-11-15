from scripts.status_guard import evaluate, marker, should_comment, status_labels


def test_zero_status_labels_warns():
    warning = evaluate(["type:task", "agent:claude"], "open", True)
    assert warning is not None
    assert marker([]) in warning
    assert "<!-- status-guard:state=none -->" in warning


def test_exactly_one_status_label_no_warning():
    assert evaluate(["type:task", "status:ready"], "open", True) is None


def test_two_status_labels_warns_with_sorted_marker():
    labels = ["type:task", "status:ready", "status:in-progress"]
    warning = evaluate(labels, "open", True)
    assert warning is not None
    assert "<!-- status-guard:state=status:in-progress,status:ready -->" in warning


def test_three_status_labels_warns():
    labels = ["status:a", "status:b", "status:c", "type:task"]
    warning = evaluate(labels, "open", True)
    assert warning is not None
    assert marker(["status:c", "status:a", "status:b"]) in warning


def test_closed_issue_no_warning():
    assert evaluate(["type:task"], "closed", True) is None
    assert evaluate(["type:task", "status:a", "status:b"], "closed", True) is None


def test_non_task_issue_no_warning():
    assert evaluate([], "open", False) is None
    assert evaluate(["status:a", "status:b"], "open", False) is None


def test_marker_is_order_independent():
    assert marker(["status:b", "status:a"]) == marker(["status:a", "status:b"])
    assert marker([]) == "<!-- status-guard:state=none -->"


def test_status_labels_filters_prefix():
    assert status_labels(["type:task", "status:b", "statusx", "status:a"]) == [
        "status:a",
        "status:b",
    ]


def test_should_comment_dedupes_by_marker():
    m = marker(["status:a", "status:b"])
    assert should_comment([], m) is True
    assert should_comment(["unrelated", marker([])], m) is True
    assert should_comment(["hello", f"{m}\nwarning text"], m) is False


def test_repeated_state_posts_once():
    labels = ["type:task", "status:a", "status:b"]
    posted = []
    for _ in range(3):
        body = evaluate(labels, "open", True)
        if body and should_comment(posted, marker(status_labels(labels))):
            posted.append(body)
    assert len(posted) == 1
