from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from local_issue_runner import parent_validation
from local_issue_runner.cli import main
from local_issue_runner.parent_validation import (
    GhGraphQLClient,
    ParentIssueInputError,
    validate_parent_issue,
)


ISSUE_URL = "https://github.com/owner/project/issues/100"


@dataclass
class FakeGraphQL:
    parent_pages: list[list[dict[str, object]]]
    dependencies: dict[int, list[list[dict[str, object]]]]
    parent_state: str = "OPEN"
    parent_parent: dict[str, object] | None = None
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def execute(self, query: str, variables: dict[str, object]) -> dict[str, object]:
        self.calls.append((query, variables))
        if "ParentIssuePage" in query:
            page = int(variables.get("cursor") or 0)
            nodes = self.parent_pages[page]
            return {
                "data": {
                    "repository": {
                        "issue": {
                            "number": 100,
                            "title": "Feature",
                            "state": self.parent_state,
                            "parent": self.parent_parent,
                            "subIssues": {
                                "nodes": nodes,
                                "pageInfo": {
                                    "hasNextPage": page + 1 < len(self.parent_pages),
                                    "endCursor": (
                                        str(page + 1)
                                        if page + 1 < len(self.parent_pages)
                                        else None
                                    ),
                                },
                            },
                        }
                    }
                }
            }
        number = int(variables["number"])
        page = int(variables.get("cursor") or 0)
        nodes = self.dependencies[number][page]
        return {
            "data": {
                "repository": {
                    "issue": {
                        "number": number,
                        "blockedBy": {
                            "nodes": nodes,
                            "pageInfo": {
                                "hasNextPage": page + 1 < len(self.dependencies[number]),
                                "endCursor": (
                                    str(page + 1)
                                    if page + 1 < len(self.dependencies[number])
                                    else None
                                ),
                            },
                        },
                    }
                }
            }
        }


def issue(
    number: int,
    *,
    state: str = "OPEN",
    repository: str = "owner/project",
    parent: int = 100,
    sub_issue_count: int = 0,
) -> dict[str, object]:
    owner, name = repository.split("/", 1)
    return {
        "number": number,
        "title": f"Issue {number}",
        "state": state,
        "repository": {"nameWithOwner": f"{owner}/{name}"},
        "parent": {
            "number": parent,
            "repository": {"nameWithOwner": "owner/project"},
        },
        "subIssues": {"totalCount": sub_issue_count},
    }


def dependency(number: int, repository: str = "owner/project") -> dict[str, object]:
    return {
        "number": number,
        "repository": {"nameWithOwner": repository},
    }


def valid_graph() -> FakeGraphQL:
    return FakeGraphQL(
        parent_pages=[[issue(101, state="CLOSED"), issue(102)]],
        dependencies={101: [[]], 102: [[dependency(101)]]},
    )


def finding_codes(report: object) -> set[str]:
    return {finding.code for finding in report.findings}  # type: ignore[attr-defined]


def test_valid_dag_reports_ready_open_children() -> None:
    report = validate_parent_issue(ISSUE_URL, github=valid_graph())

    assert report.valid
    assert report.ready_issues == (102,)
    assert report.children[1].prerequisites == (101,)


def test_empty_children_is_invalid() -> None:
    report = validate_parent_issue(
        ISSUE_URL, github=FakeGraphQL(parent_pages=[[]], dependencies={})
    )

    assert not report.valid
    assert "NO_DIRECT_CHILDREN" in finding_codes(report)


def test_nested_child_is_invalid() -> None:
    github = FakeGraphQL(
        parent_pages=[[issue(101, sub_issue_count=1)]],
        dependencies={101: [[]]},
    )

    report = validate_parent_issue(ISSUE_URL, github=github)

    assert not report.valid
    assert "NESTED_CHILD" in finding_codes(report)
    assert "remove its sub-issues" in report.findings[0].suggestion.lower()


def test_out_of_scope_dependency_is_invalid() -> None:
    github = FakeGraphQL(
        parent_pages=[[issue(101)]],
        dependencies={101: [[dependency(999, "other/project")]]},
    )

    report = validate_parent_issue(ISSUE_URL, github=github)

    assert not report.valid
    assert "CROSS_REPOSITORY_PREREQUISITE" in finding_codes(report)
    assert report.children[0].prerequisites == (999,)


def test_cycle_is_deterministic_and_identifies_affected_issues() -> None:
    github = FakeGraphQL(
        parent_pages=[[issue(102), issue(101)]],
        dependencies={
            101: [[dependency(102)]],
            102: [[dependency(101)]],
        },
    )

    report = validate_parent_issue(ISSUE_URL, github=github)

    cycle = next(finding for finding in report.findings if finding.code == "DEPENDENCY_CYCLE")
    assert cycle.issue_numbers == (101, 102)
    assert report.ready_issues == ()
    assert "NO_READY_OPEN_CHILD" not in finding_codes(report)


def test_malformed_url_is_a_tool_input_error() -> None:
    with pytest.raises(ParentIssueInputError, match="canonical GitHub issue URL"):
        validate_parent_issue("owner/project#100", github=valid_graph())


def test_malformed_url_cli_exits_two_without_success_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(["validate-parent", "owner/project#100", "--json"])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.startswith("ERROR:")


def test_human_output_summarizes_result_and_suggestions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(parent_validation, "default_graphql_client", valid_graph)

    result = main(["validate-parent", ISSUE_URL])

    output = capsys.readouterr().out
    assert result == 0
    assert output.startswith("VALID")
    assert "Parent: #100 Feature (OPEN)" in output
    assert "Children: 2" in output
    assert "Ready issues: #102" in output


def test_json_output_matches_report_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(parent_validation, "default_graphql_client", valid_graph)

    result = main(["validate-parent", ISSUE_URL, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["schema_version"] == 1
    assert payload["issue_url"] == ISSUE_URL
    assert payload["repository"] == "owner/project"
    assert payload["parent"] == {"number": 100, "title": "Feature", "state": "OPEN"}
    assert payload["children"][1]["prerequisite_issue_numbers"] == [101]
    assert payload["ready_issues"] == [102]
    assert payload["valid"] is True
    assert payload["findings"] == []


def test_gh_graphql_command_is_read_only_and_uses_structured_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def fake_run(command: tuple[str, ...], **kwargs: Any) -> object:
        calls.append((command, kwargs))
        return type(
            "Completed",
            (),
            {"returncode": 0, "stdout": '{"data": {"viewer": {"login": "octo"}}}', "stderr": ""},
        )()

    monkeypatch.setattr(parent_validation.subprocess, "run", fake_run)

    result = GhGraphQLClient().execute(
        "query Viewer($owner: String!) { viewer { login } }", {"owner": "owner"}
    )

    assert result["data"] == {"viewer": {"login": "octo"}}
    assert calls[0][0] == ("gh", "api", "graphql", "--input", "-")
    assert json.loads(calls[0][1]["input"]) == {
        "query": "query Viewer($owner: String!) { viewer { login } }",
        "variables": {"owner": "owner"},
    }
    assert "--method" not in calls[0][0]


def test_sub_issue_and_dependency_reads_are_fully_paginated() -> None:
    github = FakeGraphQL(
        parent_pages=[[issue(101)], [issue(102)]],
        dependencies={101: [[], []], 102: [[], []]},
    )

    report = validate_parent_issue(ISSUE_URL, github=github)

    assert report.valid
    parent_calls = [variables for query, variables in github.calls if "ParentIssuePage" in query]
    dependency_calls = [
        variables for query, variables in github.calls if "ChildDependenciesPage" in query
    ]
    assert [call["cursor"] for call in parent_calls] == [None, "1"]
    assert [(call["number"], call["cursor"]) for call in dependency_calls] == [
        (101, None),
        (101, "1"),
        (102, None),
        (102, "1"),
    ]
