from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

from local_issue_runner.models import (
    ChildPRIdentity,
    ChildPRRequest,
    DependencyFact,
    DependencyRead,
    FeedbackItem,
    FeedbackKind,
    IssueFact,
    ThreadState,
)

if TYPE_CHECKING:
    from local_issue_runner.completion import PullRequestObservation


class GitHubInspectionError(RuntimeError):
    """Required GitHub facts could not be read through a structured interface."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class BranchPolicy:
    branch: str
    strict: bool
    required_checks: tuple[str, ...]
    merge_methods: tuple[str, ...]

    @property
    def automatic_merge_safe(self) -> bool:
        return self.strict and bool(self.required_checks) and bool(self.merge_methods)


def _gh_json(*args: str) -> object:
    try:
        completed = subprocess.run(
            ("gh", *args),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitHubInspectionError(f"could not run gh: {error}") from error
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        status_match = re.search(r"\(HTTP (?P<status>\d{3})\)\s*$", detail)
        status_code = (
            int(status_match.group("status")) if status_match is not None else None
        )
        raise GitHubInspectionError(
            detail or f"gh {' '.join(args)} failed",
            status_code=status_code,
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise GitHubInspectionError("gh returned unreadable JSON") from error


class GitHubInspector:
    def inspect(
        self, repository: str, branches: tuple[str, ...]
    ) -> tuple[BranchPolicy, ...]:
        repo = _gh_json("api", f"repos/{repository}")
        if (
            not isinstance(repo, dict)
            or repo.get("full_name", "").casefold() != repository.casefold()
        ):
            raise GitHubInspectionError(
                "GitHub repository identity could not be verified"
            )
        permissions = repo.get("permissions")
        if not isinstance(permissions, dict) or not any(
            permissions.get(name) is True for name in ("push", "maintain", "admin")
        ):
            raise GitHubInspectionError(
                "authenticated actor lacks repository write permission"
            )
        merge_methods = tuple(
            name
            for name, field in (
                ("merge", "allow_merge_commit"),
                ("squash", "allow_squash_merge"),
                ("rebase", "allow_rebase_merge"),
            )
            if repo.get(field) is True
        )
        policies: list[BranchPolicy] = []

        for branch in dict.fromkeys(branches):
            encoded_branch = quote(branch, safe="")
            branch_data = _gh_json(
                "api", f"repos/{repository}/branches/{encoded_branch}"
            )
            if not isinstance(branch_data, dict) or branch_data.get("name") != branch:
                raise GitHubInspectionError(f"GitHub branch is not readable: {branch}")
            # Rulesets include inherited and organization rules and are therefore the
            # authoritative structured view for effective branch policy visibility.
            rules = _gh_json(
                "api",
                "-H",
                "X-GitHub-Api-Version: 2022-11-28",
                f"repos/{repository}/rules/branches/{encoded_branch}",
            )
            if not isinstance(rules, list):
                raise GitHubInspectionError(
                    f"effective branch-protection rules are unreadable for {branch}"
                )
            strict = False
            checks: set[str] = set()
            for rule in rules:
                if not isinstance(rule, dict):
                    raise GitHubInspectionError(
                        f"effective branch-protection rule is malformed for {branch}"
                    )
                parameters = rule.get("parameters")
                if rule.get("type") == "required_status_checks" and isinstance(
                    parameters, dict
                ):
                    strict = (
                        parameters.get("strict_required_status_checks_policy") is True
                    )
                    raw_checks = parameters.get("required_status_checks", [])
                    if isinstance(raw_checks, list):
                        checks.update(
                            check["context"]
                            for check in raw_checks
                            if isinstance(check, dict)
                            and isinstance(check.get("context"), str)
                        )

            if branch_data.get("protected") is True:
                try:
                    protection = _gh_json(
                        "api",
                        f"repos/{repository}/branches/{encoded_branch}/protection",
                    )
                except GitHubInspectionError as error:
                    if error.status_code != 404:
                        raise
                else:
                    if not isinstance(protection, dict):
                        raise GitHubInspectionError(
                            f"classic branch protection is unreadable for {branch}"
                        )
                    status_checks = protection.get("required_status_checks")
                    if isinstance(status_checks, dict):
                        strict = strict or status_checks.get("strict") is True
                        contexts = status_checks.get("contexts", [])
                        if isinstance(contexts, list):
                            checks.update(
                                item for item in contexts if isinstance(item, str)
                            )

            policies.append(
                BranchPolicy(branch, strict, tuple(sorted(checks)), merge_methods)
            )
        return tuple(policies)


def _object_list(value: object, context: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise GitHubInspectionError(f"GitHub returned malformed {context} data")
    return value


def _paged_objects(value: object, context: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise GitHubInspectionError(f"GitHub returned malformed {context} pages")
    objects: list[dict[str, object]] = []
    for page in value:
        objects.extend(_object_list(page, context))
    return objects


def _issue_number(value: dict[str, object], context: str) -> int:
    number = value.get("number")
    if isinstance(number, bool) or not isinstance(number, int):
        raise GitHubInspectionError(
            f"GitHub returned a malformed {context} issue number"
        )
    return number


class GitHubFactReader:
    """Read the authoritative issue facts required to rebuild a feature graph."""

    def list_sub_issues(self, repository: str, parent: int) -> tuple[int, ...]:
        raw = _gh_json(
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/issues/{parent}/sub_issues?per_page=100",
        )
        issues = _paged_objects(raw, "sub-issue")
        return tuple(_issue_number(item, "sub-issue") for item in issues)

    def get_issue(self, repository: str, number: int) -> IssueFact:
        raw = _gh_json("api", f"repos/{repository}/issues/{number}")
        if not isinstance(raw, dict):
            raise GitHubInspectionError("GitHub returned malformed issue data")
        title = raw.get("title")
        body = raw.get("body")
        state = raw.get("state")
        updated_at = raw.get("updated_at")
        if (
            not isinstance(title, str)
            or (body is not None and not isinstance(body, str))
            or not isinstance(state, str)
        ):
            raise GitHubInspectionError(f"GitHub returned malformed issue {number}")
        return IssueFact(
            repository=repository,
            number=_issue_number(raw, "issue"),
            title=title,
            state=state.upper(),
            source_revision=updated_at if isinstance(updated_at, str) else None,
            body=body or "",
        )

    def read_dependencies(self, repository: str, number: int) -> DependencyRead:
        raw = _gh_json(
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/issues/{number}/dependencies/blocked_by?per_page=100",
        )
        dependencies: list[DependencyFact] = []
        for item in _paged_objects(raw, "dependency"):
            updated_at = item.get("updated_at")
            dependencies.append(
                DependencyFact(
                    repository=repository,
                    prerequisite=_issue_number(item, "dependency"),
                    dependent=number,
                    source_revision=updated_at if isinstance(updated_at, str) else None,
                )
            )
        return DependencyRead(complete=True, edges=tuple(dependencies))


@dataclass(frozen=True, slots=True)
class ChildPullRequest:
    number: int
    state: str
    head_ref: str
    head_sha: str
    base_ref: str
    body: str
    linked_issue: int
    merged: bool = False


def child_pr_ownership_marker(identity: ChildPRIdentity) -> str:
    payload = json.dumps(
        {
            "repository": identity.repository,
            "parent_issue": identity.parent_issue,
            "issue_number": identity.issue_number,
            "delivery": identity.delivery,
            "work_id": identity.work_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"<!-- local-issue-runner:{payload} -->"


def _child_pull_request(raw: dict[str, object], linked_issue: int) -> ChildPullRequest:
    head = raw.get("head")
    base = raw.get("base")
    if (
        isinstance(raw.get("number"), bool)
        or not isinstance(raw.get("number"), int)
        or not isinstance(head, dict)
        or not isinstance(base, dict)
        or not isinstance(head.get("ref"), str)
        or not isinstance(head.get("sha"), str)
        or not isinstance(base.get("ref"), str)
    ):
        raise GitHubInspectionError("GitHub returned malformed pull request data")
    body = raw.get("body")
    state = raw.get("state")
    number = raw["number"]
    assert isinstance(number, int) and not isinstance(number, bool)
    return ChildPullRequest(
        number=number,
        state=state.upper() if isinstance(state, str) else "UNKNOWN",
        head_ref=head["ref"],
        head_sha=head["sha"],
        base_ref=base["ref"],
        body=body if isinstance(body, str) else "",
        linked_issue=linked_issue,
        merged=raw.get("merged_at") is not None,
    )


class GitHubClient:
    def get_pull_request(self, repository: str, number: int) -> PullRequestObservation:
        from local_issue_runner.completion import PullRequestObservation

        raw = _gh_json("api", f"repos/{repository}/pulls/{number}")
        if not isinstance(raw, dict):
            raise GitHubInspectionError("GitHub returned malformed pull request data")
        parsed = _child_pull_request(raw, number)
        resulting_commit = raw.get("merge_commit_sha")
        return PullRequestObservation(
            number=parsed.number,
            state=parsed.state,
            merged=parsed.merged,
            head_sha=parsed.head_sha,
            base_ref=parsed.base_ref,
            resulting_commit=(
                resulting_commit if isinstance(resulting_commit, str) else None
            ),
        )

    def get_issue_state(self, repository: str, number: int) -> str:
        raw = _gh_json("api", f"repos/{repository}/issues/{number}")
        if not isinstance(raw, dict) or not isinstance(raw.get("state"), str):
            raise GitHubInspectionError("GitHub returned malformed issue state")
        return raw["state"].upper()

    def close_issue_as_completed(
        self, repository: str, number: int, evidence_url: str
    ) -> None:
        normalized_url = evidence_url.strip()
        if not normalized_url:
            raise ValueError("completion evidence URL is required")
        digest = hashlib.sha256(normalized_url.encode()).hexdigest()
        marker = f"<!-- local-issue-runner-completion:{digest} -->"
        comment_endpoint = f"repos/{repository}/issues/{number}/comments"
        comments = _gh_json(
            "api",
            "--paginate",
            "--slurp",
            f"{comment_endpoint}?per_page=100",
        )
        if not any(
            isinstance(comment.get("body"), str) and marker in comment["body"]
            for comment in _paged_objects(comments, "completion comment")
        ):
            posted = _gh_json(
                "api",
                "--method",
                "POST",
                comment_endpoint,
                "-f",
                f"body=Completed by Local Issue Runner.\n\nEvidence: "
                f"{normalized_url}\n\n{marker}",
            )
            if (
                not isinstance(posted, dict)
                or not isinstance(posted.get("body"), str)
                or marker not in posted["body"]
            ):
                raise GitHubInspectionError(
                    "GitHub did not confirm the completion evidence comment"
                )

        issue_endpoint = f"repos/{repository}/issues/{number}"
        issue = _gh_json("api", issue_endpoint)
        if not isinstance(issue, dict) or not isinstance(issue.get("state"), str):
            raise GitHubInspectionError("GitHub returned malformed issue state")
        if issue["state"].upper() != "CLOSED":
            issue = _gh_json(
                "api",
                "--method",
                "PATCH",
                issue_endpoint,
                "-f",
                "state=closed",
                "-f",
                "state_reason=completed",
            )
            if (
                not isinstance(issue, dict)
                or not isinstance(issue.get("state"), str)
                or issue["state"].upper() != "CLOSED"
            ):
                raise GitHubInspectionError("GitHub did not confirm issue closure")

    def list_child_pull_requests(
        self, repository: str, issue_number: int
    ) -> tuple[ChildPullRequest, ...]:
        raw = _gh_json(
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/pulls?state=all&per_page=100",
        )
        reference = re.compile(rf"(?<!\d)#{issue_number}(?!\d)")
        matches: list[ChildPullRequest] = []
        for item in _paged_objects(raw, "pull request"):
            body = item.get("body")
            if isinstance(body, str) and reference.search(body) is not None:
                matches.append(_child_pull_request(item, issue_number))
        return tuple(matches)

    def create_child_pull_request(self, candidate: ChildPRRequest) -> ChildPullRequest:
        body = candidate.body.rstrip()
        marker = child_pr_ownership_marker(candidate.identity)
        if marker not in body:
            body = f"{body}\n\n{marker}" if body else marker
        raw = _gh_json(
            "api",
            "--method",
            "POST",
            f"repos/{candidate.identity.repository}/pulls",
            "-f",
            f"title={candidate.title}",
            "-f",
            f"head={candidate.head_ref}",
            "-f",
            f"base={candidate.base_ref}",
            "-f",
            f"body={body}",
        )
        if not isinstance(raw, dict):
            raise GitHubInspectionError("GitHub returned malformed created PR data")
        return _child_pull_request(raw, candidate.identity.issue_number)

    def list_integration_pull_requests(
        self, repository: str, feature_branch: str, main_branch: str
    ) -> tuple[object, ...]:
        from local_issue_runner.integration import IntegrationPullRequest

        raw = _gh_json(
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/pulls?state=all&base={main_branch}&per_page=100",
        )
        results: list[IntegrationPullRequest] = []
        for item in _paged_objects(raw, "integration pull request"):
            parsed = _child_pull_request(item, 1)
            if parsed.head_ref == feature_branch and parsed.base_ref == main_branch:
                results.append(
                    IntegrationPullRequest(
                        parsed.number,
                        parsed.state,
                        parsed.head_ref,
                        parsed.head_sha,
                        parsed.base_ref,
                        parsed.body,
                        parsed.merged,
                    )
                )
        return tuple(results)

    def create_integration_pull_request(self, candidate: object) -> object:
        from local_issue_runner.integration import (
            IntegrationPullRequest,
            IntegrationRequest,
            integration_pr_ownership_marker,
        )

        if not isinstance(candidate, IntegrationRequest):
            raise TypeError("candidate must be an IntegrationRequest")
        body = candidate.body.rstrip()
        marker = integration_pr_ownership_marker(candidate.identity)
        if marker not in body:
            body = f"{body}\n\n{marker}" if body else marker
        raw = _gh_json(
            "api",
            "--method",
            "POST",
            f"repos/{candidate.identity.repository}/pulls",
            "-f",
            f"title={candidate.title}",
            "-f",
            f"head={candidate.feature_branch}",
            "-f",
            f"base={candidate.main_branch}",
            "-f",
            f"body={body}",
        )
        if not isinstance(raw, dict):
            raise GitHubInspectionError("GitHub returned malformed created PR data")
        parsed = _child_pull_request(raw, candidate.identity.parent_issue)
        return IntegrationPullRequest(
            parsed.number,
            parsed.state,
            parsed.head_ref,
            parsed.head_sha,
            parsed.base_ref,
            parsed.body,
            parsed.merged,
        )

    def create_integration_repair_child(
        self,
        owner: object,
        defect: object,
        *,
        attach_to_parent: bool,
    ) -> int:
        from local_issue_runner.integration import (
            IntegrationDefect,
            IntegrationIdentity,
        )

        if not isinstance(owner, IntegrationIdentity) or not isinstance(
            defect, IntegrationDefect
        ):
            raise TypeError("repair child requires integration identity and defect")
        criteria = "\n".join(f"- [ ] {item}" for item in defect.acceptance_criteria)
        body = (
            "This integration-repair child was generated from a concrete "
            f"{defect.source} finding.\n\n"
            f"**Finding identity:** `{defect.finding_identity}`\n\n"
            f"**Reproduction:** {defect.reproduction}\n\n"
            f"## Acceptance criteria\n{criteria}"
        )
        raw = _gh_json(
            "api",
            "--method",
            "POST",
            f"repos/{owner.repository}/issues",
            "-f",
            f"title={defect.title}",
            "-f",
            f"body={body}",
        )
        if not isinstance(raw, dict):
            raise GitHubInspectionError(
                "GitHub returned malformed created repair issue data"
            )
        issue_number = _issue_number(raw, "repair")
        node_id = raw.get("id")
        if attach_to_parent:
            if isinstance(node_id, bool) or not isinstance(node_id, int):
                raise GitHubInspectionError(
                    "GitHub repair issue lacks a numeric database ID"
                )
            _gh_json(
                "api",
                "--method",
                "POST",
                f"repos/{owner.repository}/issues/{owner.parent_issue}/sub_issues",
                "-F",
                f"sub_issue_id={node_id}",
            )
        return issue_number


def _required_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise GitHubInspectionError(f"GitHub returned malformed {context}")
    return value


def _feedback_marker(item: FeedbackItem, body: str) -> str:
    import hashlib

    material = f"{item.identity}\0{item.updated_at}\0{item.body}\0{body}".encode()
    digest = hashlib.sha256(material).hexdigest()
    return f"<!-- local-issue-runner-feedback:{digest} -->"


class GitHubFeedbackClient:
    """Read and mutate PR feedback through stable GitHub interfaces."""

    def __init__(self, repository: str) -> None:
        if "/" not in repository:
            raise ValueError("repository must be an owner/name identity")
        self.repository = repository

    def list_feedback(self, pr_number: int) -> tuple[FeedbackItem, ...]:
        issue_comments = _gh_json(
            "api",
            "--paginate",
            "--slurp",
            f"repos/{self.repository}/issues/{pr_number}/comments?per_page=100",
        )
        reviews = _gh_json(
            "api",
            "--paginate",
            "--slurp",
            f"repos/{self.repository}/pulls/{pr_number}/reviews?per_page=100",
        )
        query = """
        query($owner:String!,$name:String!,$number:Int!) {
          repository(owner:$owner,name:$name) {
            pullRequest(number:$number) {
              reviewThreads(first:100) {
                nodes {
                  id
                  isResolved
                  comments(first:100) {
                    nodes {
                      databaseId
                      body
                      createdAt
                      updatedAt
                      author { login }
                    }
                  }
                }
              }
            }
          }
        }
        """
        owner, name = self.repository.split("/", 1)
        threads = _gh_json(
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            "-F",
            f"number={pr_number}",
        )

        items: list[FeedbackItem] = []
        for raw in _paged_objects(issue_comments, "PR comment"):
            items.append(self._rest_feedback(raw, FeedbackKind.ISSUE_COMMENT))
        for raw in _paged_objects(reviews, "review"):
            body = raw.get("body")
            if isinstance(body, str) and body.strip():
                items.append(self._rest_feedback(raw, FeedbackKind.REVIEW_BODY))
        items.extend(self._thread_feedback(threads))
        return tuple(items)

    @staticmethod
    def _rest_feedback(raw: dict[str, object], kind: FeedbackKind) -> FeedbackItem:
        author = raw.get("user")
        item_id = raw.get("id")
        if isinstance(item_id, bool) or not isinstance(item_id, int):
            raise GitHubInspectionError("GitHub returned malformed feedback ID")
        login = author.get("login") if isinstance(author, dict) else "[deleted]"
        created_at = (
            raw.get("submitted_at")
            if kind is FeedbackKind.REVIEW_BODY
            else raw.get("created_at")
        )
        updated_at = (
            raw.get("submitted_at")
            if kind is FeedbackKind.REVIEW_BODY
            else raw.get("updated_at")
        )
        return FeedbackItem(
            kind=kind,
            item_id=str(item_id),
            author_login=login if isinstance(login, str) and login else "[deleted]",
            body=_required_string(raw.get("body"), "feedback body"),
            created_at=_required_string(created_at, "feedback timestamp"),
            updated_at=_required_string(updated_at, "feedback timestamp"),
        )

    @staticmethod
    def _thread_feedback(raw: object) -> list[FeedbackItem]:
        if not isinstance(raw, dict):
            raise GitHubInspectionError("GitHub returned malformed review-thread data")
        data = raw.get("data")
        repository = data.get("repository") if isinstance(data, dict) else None
        pull_request = (
            repository.get("pullRequest") if isinstance(repository, dict) else None
        )
        review_threads = (
            pull_request.get("reviewThreads")
            if isinstance(pull_request, dict)
            else None
        )
        nodes = (
            review_threads.get("nodes") if isinstance(review_threads, dict) else None
        )
        if nodes is None:
            raise GitHubInspectionError("GitHub returned malformed review-thread data")
        threads = _object_list(nodes, "review thread")
        items: list[FeedbackItem] = []
        for thread in threads:
            thread_id = _required_string(thread.get("id"), "review thread ID")
            resolved = thread.get("isResolved")
            comments = thread.get("comments")
            if not isinstance(resolved, bool) or not isinstance(comments, dict):
                raise GitHubInspectionError("GitHub returned malformed review thread")
            for comment in _object_list(comments.get("nodes"), "review comment"):
                database_id = comment.get("databaseId")
                author = comment.get("author")
                if isinstance(database_id, bool) or not isinstance(database_id, int):
                    raise GitHubInspectionError(
                        "GitHub returned malformed review comment ID"
                    )
                login = author.get("login") if isinstance(author, dict) else "[deleted]"
                items.append(
                    FeedbackItem(
                        kind=FeedbackKind.REVIEW_THREAD_COMMENT,
                        item_id=str(database_id),
                        author_login=(
                            login if isinstance(login, str) and login else "[deleted]"
                        ),
                        body=_required_string(
                            comment.get("body"), "review comment body"
                        ),
                        created_at=_required_string(
                            comment.get("createdAt"), "review comment timestamp"
                        ),
                        updated_at=_required_string(
                            comment.get("updatedAt"), "review comment timestamp"
                        ),
                        thread_id=thread_id,
                        thread_state=(
                            ThreadState.RESOLVED if resolved else ThreadState.OPEN
                        ),
                    )
                )
        return items

    def reply(self, pr_number: int, item: FeedbackItem, body: str) -> str:
        marker = _feedback_marker(item, body)
        rendered = f"{body.rstrip()}\n\n{marker}"
        if item.kind is FeedbackKind.REVIEW_THREAD_COMMENT:
            endpoint = f"repos/{self.repository}/pulls/{pr_number}/comments"
            existing = self._find_marked_comment(endpoint, marker)
            if existing is None:
                raw = _gh_json(
                    "api",
                    "--method",
                    "POST",
                    f"{endpoint}/{item.item_id}/replies",
                    "-f",
                    f"body={rendered}",
                )
                existing = self._comment_id(raw)
            return f"{FeedbackKind.REVIEW_THREAD_COMMENT.value}:{existing}"

        endpoint = f"repos/{self.repository}/issues/{pr_number}/comments"
        existing = self._find_marked_comment(endpoint, marker)
        if existing is None:
            raw = _gh_json(
                "api", "--method", "POST", endpoint, "-f", f"body={rendered}"
            )
            existing = self._comment_id(raw)
        return f"{FeedbackKind.ISSUE_COMMENT.value}:{existing}"

    @staticmethod
    def _comment_id(raw: object) -> str:
        if not isinstance(raw, dict):
            raise GitHubInspectionError("GitHub returned malformed posted comment")
        value = raw.get("id")
        if isinstance(value, bool) or not isinstance(value, int):
            raise GitHubInspectionError("GitHub returned malformed posted comment ID")
        return str(value)

    def _find_marked_comment(self, endpoint: str, marker: str) -> str | None:
        raw = _gh_json("api", "--paginate", "--slurp", f"{endpoint}?per_page=100")
        for comment in _paged_objects(raw, "comment"):
            body = comment.get("body")
            if isinstance(body, str) and marker in body:
                return self._comment_id(comment)
        return None

    def resolve_thread(self, thread_id: str) -> None:
        mutation = """
        mutation($threadId:ID!) {
          resolveReviewThread(input:{threadId:$threadId}) {
            thread { id isResolved }
          }
        }
        """
        raw = _gh_json(
            "api",
            "graphql",
            "-f",
            f"query={mutation}",
            "-f",
            f"threadId={thread_id}",
        )
        if not isinstance(raw, dict) or raw.get("errors"):
            raise GitHubInspectionError("GitHub could not resolve the review thread")
        data = raw.get("data")
        resolution = data.get("resolveReviewThread") if isinstance(data, dict) else None
        thread = resolution.get("thread") if isinstance(resolution, dict) else None
        if (
            not isinstance(thread, dict)
            or thread.get("id") != thread_id
            or thread.get("isResolved") is not True
        ):
            raise GitHubInspectionError(
                "GitHub did not confirm review-thread resolution"
            )
