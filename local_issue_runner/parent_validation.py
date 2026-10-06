from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Protocol

from local_issue_runner.github_api import GitHubInspectionError


_ISSUE_URL = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repository>[A-Za-z0-9_.-]+)/issues/(?P<number>[1-9][0-9]*)"
)

_PARENT_QUERY = """
query ParentIssuePage(
  $owner: String!
  $repository: String!
  $number: Int!
  $cursor: String
) {
  repository(owner: $owner, name: $repository) {
    issue(number: $number) {
      number
      title
      state
      parent {
        number
        repository { nameWithOwner }
      }
      subIssues(first: 100, after: $cursor) {
        nodes {
          number
          title
          state
          repository { nameWithOwner }
          parent {
            number
            repository { nameWithOwner }
          }
          subIssues(first: 1) { totalCount }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

_DEPENDENCIES_QUERY = """
query ChildDependenciesPage(
  $owner: String!
  $repository: String!
  $number: Int!
  $cursor: String
) {
  repository(owner: $owner, name: $repository) {
    issue(number: $number) {
      number
      blockedBy(first: 100, after: $cursor) {
        nodes {
          number
          repository { nameWithOwner }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()


class ParentIssueInputError(ValueError):
    """The requested parent issue URL is not a supported canonical URL."""


class GraphQLClient(Protocol):
    def execute(
        self, query: str, variables: dict[str, object]
    ) -> dict[str, object]: ...


class GhGraphQLClient:
    """Read GitHub GraphQL through the authenticated GitHub CLI."""

    def execute(
        self, query: str, variables: dict[str, object]
    ) -> dict[str, object]:
        payload = json.dumps({"query": query, "variables": variables})
        try:
            completed = subprocess.run(
                ("gh", "api", "graphql", "--input", "-"),
                input=payload,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitHubInspectionError(
                f"could not run gh GraphQL query: {error}"
            ) from error
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise GitHubInspectionError(detail or "gh GraphQL query failed")
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise GitHubInspectionError("gh returned unreadable GraphQL JSON") from error
        if not isinstance(result, dict):
            raise GitHubInspectionError("gh returned malformed GraphQL data")
        errors = result.get("errors")
        if errors:
            raise GitHubInspectionError(f"GitHub GraphQL query failed: {errors}")
        return result


def default_graphql_client() -> GraphQLClient:
    return GhGraphQLClient()


@dataclass(frozen=True, slots=True)
class IssueSummary:
    number: int
    title: str
    state: str

    def to_dict(self) -> dict[str, object]:
        return {"number": self.number, "title": self.title, "state": self.state}


@dataclass(frozen=True, slots=True)
class ChildSummary:
    number: int
    title: str
    state: str
    prerequisites: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "prerequisite_issue_numbers": list(self.prerequisites),
        }


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    code: str
    severity: str
    message: str
    issue_numbers: tuple[int, ...]
    suggestion: str

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "issue_numbers": list(self.issue_numbers),
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True, slots=True)
class ParentIssueValidationReport:
    issue_url: str
    repository: str
    parent: IssueSummary
    children: tuple[ChildSummary, ...]
    ready_issues: tuple[int, ...]
    valid: bool
    findings: tuple[ValidationFinding, ...]
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "issue_url": self.issue_url,
            "repository": self.repository,
            "parent": self.parent.to_dict(),
            "children": [child.to_dict() for child in self.children],
            "ready_issues": list(self.ready_issues),
            "valid": self.valid,
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True, slots=True)
class _ParsedChild:
    summary: IssueSummary
    repository: str
    parent_number: int | None
    parent_repository: str | None
    sub_issue_count: int


def _canonical_issue_url(issue_url: str) -> tuple[str, str, int]:
    match = _ISSUE_URL.fullmatch(issue_url)
    if match is None:
        raise ParentIssueInputError(
            "ISSUE_URL must be a canonical GitHub issue URL such as "
            "https://github.com/owner/repository/issues/123"
        )
    return match["owner"], match["repository"], int(match["number"])


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GitHubInspectionError(f"GitHub returned malformed {context}")
    return value


def _positive_number(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GitHubInspectionError(f"GitHub returned malformed {context} number")
    return value


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise GitHubInspectionError(f"GitHub returned malformed {context}")
    return value


def _issue_from_response(
    result: dict[str, object], expected_number: int, context: str
) -> dict[str, object]:
    data = _mapping(result.get("data"), f"{context} data")
    repository = _mapping(data.get("repository"), f"{context} repository")
    raw_issue = repository.get("issue")
    if raw_issue is None:
        raise GitHubInspectionError(f"GitHub issue #{expected_number} was not found")
    issue = _mapping(raw_issue, context)
    number = _positive_number(issue.get("number"), context)
    if number != expected_number:
        raise GitHubInspectionError(
            f"GitHub returned issue #{number} while reading issue #{expected_number}"
        )
    return issue


def _page(
    connection_value: object, context: str
) -> tuple[list[dict[str, object]], str | None, bool]:
    connection = _mapping(connection_value, f"{context} connection")
    raw_nodes = connection.get("nodes")
    if not isinstance(raw_nodes, list) or any(
        not isinstance(node, dict) for node in raw_nodes
    ):
        raise GitHubInspectionError(f"GitHub returned malformed {context} nodes")
    page_info = _mapping(connection.get("pageInfo"), f"{context} page information")
    has_next = page_info.get("hasNextPage")
    cursor = page_info.get("endCursor")
    if not isinstance(has_next, bool):
        raise GitHubInspectionError(f"GitHub returned malformed {context} pagination")
    if has_next and (not isinstance(cursor, str) or not cursor):
        return raw_nodes, None, False
    if not has_next:
        cursor = None
    return raw_nodes, cursor if isinstance(cursor, str) else None, True


def _parent_reference(value: object) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    parent = _mapping(value, "parent relationship")
    repository = _mapping(parent.get("repository"), "parent repository")
    return (
        _positive_number(parent.get("number"), "parent"),
        _text(repository.get("nameWithOwner"), "parent repository identity"),
    )


def _parse_child(value: dict[str, object]) -> _ParsedChild:
    repository = _mapping(value.get("repository"), "child repository")
    sub_issues = _mapping(value.get("subIssues"), "child sub-issues")
    count = sub_issues.get("totalCount")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise GitHubInspectionError("GitHub returned malformed child sub-issue count")
    parent_number, parent_repository = _parent_reference(value.get("parent"))
    return _ParsedChild(
        summary=IssueSummary(
            _positive_number(value.get("number"), "child issue"),
            _text(value.get("title"), "child title"),
            _text(value.get("state"), "child state").upper(),
        ),
        repository=_text(
            repository.get("nameWithOwner"), "child repository identity"
        ),
        parent_number=parent_number,
        parent_repository=parent_repository,
        sub_issue_count=count,
    )


def _cycles(
    child_numbers: tuple[int, ...], prerequisites: dict[int, tuple[int, ...]]
) -> tuple[tuple[int, ...], ...]:
    index = 0
    indexes: dict[int, int] = {}
    low_links: dict[int, int] = {}
    stack: list[int] = []
    on_stack: set[int] = set()
    components: list[tuple[int, ...]] = []
    selected = set(child_numbers)

    def visit(node: int) -> None:
        nonlocal index
        indexes[node] = index
        low_links[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for prerequisite in sorted(
            candidate for candidate in prerequisites[node] if candidate in selected
        ):
            if prerequisite not in indexes:
                visit(prerequisite)
                low_links[node] = min(low_links[node], low_links[prerequisite])
            elif prerequisite in on_stack:
                low_links[node] = min(low_links[node], indexes[prerequisite])
        if low_links[node] != indexes[node]:
            return
        component: list[int] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        ordered = tuple(sorted(component))
        if len(ordered) > 1 or (
            len(ordered) == 1 and ordered[0] in prerequisites[ordered[0]]
        ):
            components.append(ordered)

    for number in sorted(child_numbers):
        if number not in indexes:
            visit(number)
    return tuple(sorted(components))


def validate_parent_issue(
    issue_url: str, *, github: GraphQLClient | None = None
) -> ParentIssueValidationReport:
    owner, repository_name, parent_number = _canonical_issue_url(issue_url)
    repository = f"{owner}/{repository_name}"
    client = github or default_graphql_client()
    findings: list[ValidationFinding] = []
    raw_children: list[dict[str, object]] = []
    cursor: str | None = None
    parent_issue: dict[str, object] | None = None

    while True:
        result = client.execute(
            _PARENT_QUERY,
            {
                "owner": owner,
                "repository": repository_name,
                "number": parent_number,
                "cursor": cursor,
            },
        )
        issue = _issue_from_response(result, parent_number, "parent issue")
        if parent_issue is None:
            parent_issue = issue
        nodes, next_cursor, complete = _page(issue.get("subIssues"), "sub-issue")
        raw_children.extend(nodes)
        if not complete:
            findings.append(
                ValidationFinding(
                    "INCOMPLETE_SUB_ISSUE_READ",
                    "error",
                    "GitHub indicated another sub-issue page but did not provide a cursor.",
                    (parent_number,),
                    "Retry after confirming the authenticated gh CLI can read all sub-issues.",
                )
            )
            break
        if next_cursor is None:
            break
        cursor = next_cursor

    assert parent_issue is not None
    parent = IssueSummary(
        _positive_number(parent_issue.get("number"), "parent issue"),
        _text(parent_issue.get("title"), "parent title"),
        _text(parent_issue.get("state"), "parent state").upper(),
    )
    parent_of_parent, _ = _parent_reference(parent_issue.get("parent"))
    if parent_of_parent is not None:
        findings.append(
            ValidationFinding(
                "PARENT_NOT_TOP_LEVEL",
                "error",
                f"Parent issue #{parent.number} is itself a sub-issue of #{parent_of_parent}.",
                (parent.number, parent_of_parent),
                f"Remove issue #{parent.number} from parent #{parent_of_parent} before using it as a runner parent.",
            )
        )
    if parent.state != "OPEN":
        findings.append(
            ValidationFinding(
                "PARENT_NOT_OPEN",
                "error",
                f"Parent issue #{parent.number} is {parent.state}.",
                (parent.number,),
                f"Reopen issue #{parent.number}.",
            )
        )
    if not raw_children:
        findings.append(
            ValidationFinding(
                "NO_DIRECT_CHILDREN",
                "error",
                f"Parent issue #{parent.number} has no direct sub-issues.",
                (parent.number,),
                f"Add at least one direct sub-issue to issue #{parent.number}.",
            )
        )

    parsed_children = sorted(
        (_parse_child(child) for child in raw_children),
        key=lambda child: child.summary.number,
    )
    counts: dict[int, int] = {}
    for child in parsed_children:
        counts[child.summary.number] = counts.get(child.summary.number, 0) + 1
    duplicate_numbers = tuple(
        number for number, count in sorted(counts.items()) if count > 1
    )
    if duplicate_numbers:
        findings.append(
            ValidationFinding(
                "DUPLICATE_CHILD",
                "error",
                "The direct sub-issue list contains duplicate issue entries.",
                duplicate_numbers,
                "Remove and re-add the listed sub-issues so each appears exactly once.",
            )
        )

    unique_children: dict[int, _ParsedChild] = {}
    readiness_blocked: set[int] = set()
    for child in parsed_children:
        number = child.summary.number
        unique_children.setdefault(number, child)
        if number == parent.number:
            findings.append(
                ValidationFinding(
                    "PARENT_LISTED_AS_CHILD",
                    "error",
                    f"Parent issue #{parent.number} is listed as its own child.",
                    (parent.number,),
                    f"Remove issue #{parent.number} from its own sub-issue list.",
                )
            )
            readiness_blocked.add(number)
        if child.repository.casefold() != repository.casefold():
            findings.append(
                ValidationFinding(
                    "CROSS_REPOSITORY_CHILD",
                    "error",
                    f"Child issue #{number} belongs to {child.repository}, not {repository}.",
                    (number,),
                    f"Detach issue #{number} and add a child issue from {repository}.",
                )
            )
            readiness_blocked.add(number)
        if (
            child.parent_number != parent.number
            or child.parent_repository is None
            or child.parent_repository.casefold() != repository.casefold()
        ):
            actual = (
                f"{child.parent_repository}#{child.parent_number}"
                if child.parent_number is not None
                else "no parent"
            )
            findings.append(
                ValidationFinding(
                    "CHILD_NOT_DIRECT",
                    "error",
                    f"Child issue #{number} reports {actual} instead of direct parent {repository}#{parent.number}.",
                    (number, parent.number),
                    f"Set issue #{parent.number} as the direct parent of issue #{number}.",
                )
            )
            readiness_blocked.add(number)
        if child.sub_issue_count:
            findings.append(
                ValidationFinding(
                    "NESTED_CHILD",
                    "error",
                    f"Child issue #{number} has {child.sub_issue_count} sub-issue(s).",
                    (number,),
                    f"Flatten issue #{number}: remove its sub-issues or make them direct children of #{parent.number}.",
                )
            )
            readiness_blocked.add(number)

    selected = set(unique_children)
    prerequisites: dict[int, tuple[int, ...]] = {}
    for number in sorted(unique_children):
        raw_dependencies: list[dict[str, object]] = []
        dependency_cursor: str | None = None
        complete = True
        while True:
            result = client.execute(
                _DEPENDENCIES_QUERY,
                {
                    "owner": owner,
                    "repository": repository_name,
                    "number": number,
                    "cursor": dependency_cursor,
                },
            )
            issue = _issue_from_response(result, number, "child dependency issue")
            nodes, next_cursor, page_complete = _page(
                issue.get("blockedBy"), "blocked-by dependency"
            )
            raw_dependencies.extend(nodes)
            if not page_complete:
                complete = False
                break
            if next_cursor is None:
                break
            dependency_cursor = next_cursor
        if not complete:
            findings.append(
                ValidationFinding(
                    "INCOMPLETE_DEPENDENCY_READ",
                    "error",
                    f"Dependency pagination was incomplete for child issue #{number}.",
                    (number,),
                    f"Retry after confirming the authenticated gh CLI can read all dependencies for issue #{number}.",
                )
            )
            readiness_blocked.add(number)

        numbers: list[int] = []
        seen_dependencies: set[tuple[str, int]] = set()
        for raw_dependency in raw_dependencies:
            dependency_repository = _mapping(
                raw_dependency.get("repository"), "dependency repository"
            )
            dependency_repo = _text(
                dependency_repository.get("nameWithOwner"),
                "dependency repository identity",
            )
            prerequisite = _positive_number(
                raw_dependency.get("number"), "dependency issue"
            )
            key = (dependency_repo.casefold(), prerequisite)
            if key in seen_dependencies:
                continue
            seen_dependencies.add(key)
            numbers.append(prerequisite)
            if dependency_repo.casefold() != repository.casefold():
                findings.append(
                    ValidationFinding(
                        "CROSS_REPOSITORY_PREREQUISITE",
                        "error",
                        (
                            f"Issue #{number} is blocked by "
                            f"{dependency_repo}#{prerequisite}; runner dependencies "
                            f"must stay in {repository}."
                        ),
                        (number, prerequisite),
                        (
                            f"Remove the dependency from "
                            f"{dependency_repo}#{prerequisite} to "
                            f"{repository}#{number}, or replace it with a selected "
                            f"child in {repository}."
                        ),
                    )
                )
                readiness_blocked.add(number)
            elif prerequisite not in selected:
                findings.append(
                    ValidationFinding(
                        "OUT_OF_SCOPE_PREREQUISITE",
                        "error",
                        f"Issue #{number} is blocked by unselected issue #{prerequisite}.",
                        (number, prerequisite),
                        f"Add issue #{prerequisite} as a direct child of #{parent.number}, or remove it as a prerequisite of issue #{number}.",
                    )
                )
                readiness_blocked.add(number)
        prerequisites[number] = tuple(sorted(set(numbers)))

    child_numbers = tuple(sorted(unique_children))
    cycles = _cycles(child_numbers, prerequisites)
    cycle_numbers = tuple(sorted({number for cycle in cycles for number in cycle}))
    if cycles:
        descriptions = ", ".join(
            "{" + ", ".join(f"#{number}" for number in cycle) + "}"
            for cycle in cycles
        )
        findings.append(
            ValidationFinding(
                "DEPENDENCY_CYCLE",
                "error",
                (
                    f"Dependency cycle(s) affect issue set(s) {descriptions}; "
                    "edges use prerequisite -> dependent semantics."
                ),
                cycle_numbers,
                "Remove or reverse one blocking relationship in each listed cycle so prerequisites flow toward dependents.",
            )
        )
        readiness_blocked.update(cycle_numbers)

    states = {number: child.summary.state for number, child in unique_children.items()}
    ready = tuple(
        number
        for number in child_numbers
        if states[number] == "OPEN"
        and number not in readiness_blocked
        and all(
            prerequisite in states and states[prerequisite] == "CLOSED"
            for prerequisite in prerequisites[number]
        )
    )
    open_children = tuple(
        number for number in child_numbers if states[number] == "OPEN"
    )
    if open_children and not ready and not cycles:
        findings.append(
            ValidationFinding(
                "NO_READY_OPEN_CHILD",
                "error",
                "The graph contains open child issues but none has all selected prerequisites resolved.",
                open_children,
                "Close a completed prerequisite, remove an invalid blocking relationship, or make one open child independent.",
            )
        )

    children = tuple(
        ChildSummary(
            child.summary.number,
            child.summary.title,
            child.summary.state,
            prerequisites[child.summary.number],
        )
        for child in (unique_children[number] for number in child_numbers)
    )
    return ParentIssueValidationReport(
        issue_url=issue_url,
        repository=repository,
        parent=parent,
        children=children,
        ready_issues=ready,
        valid=not any(finding.severity == "error" for finding in findings),
        findings=tuple(findings),
    )


def render_parent_validation(report: ParentIssueValidationReport) -> str:
    lines = [
        f"{'VALID' if report.valid else 'INVALID'} parent issue structure",
        f"Repository: {report.repository}",
        f"Parent: #{report.parent.number} {report.parent.title} ({report.parent.state})",
        f"Children: {len(report.children)}",
        "Ready issues: "
        + (
            ", ".join(f"#{number}" for number in report.ready_issues)
            if report.ready_issues
            else "none"
        ),
    ]
    for child in report.children:
        prerequisites = (
            ", ".join(f"#{number}" for number in child.prerequisites)
            if child.prerequisites
            else "none"
        )
        lines.append(
            f"- Child #{child.number}: {child.title} ({child.state}); prerequisites: {prerequisites}"
        )
    if report.findings:
        lines.append("Findings:")
        for finding in report.findings:
            issues = (
                ", ".join(f"#{number}" for number in finding.issue_numbers)
                or "none"
            )
            lines.append(
                f"- {finding.severity.upper()} [{finding.code}] issues {issues}: {finding.message}"
            )
            lines.append(f"  Suggested change: {finding.suggestion}")
    else:
        lines.append("Findings: none")
    return "\n".join(lines)
