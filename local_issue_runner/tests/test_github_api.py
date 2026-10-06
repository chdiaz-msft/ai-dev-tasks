from __future__ import annotations

import pytest

import local_issue_runner.github_api as github_api
from local_issue_runner.github_api import GitHubInspectionError, GitHubInspector


def test_inspector_url_encodes_branch_path_components(
    monkeypatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    repository = "owner/project"
    branch = "feature/issue-353"
    encoded_branch = "feature%2Fissue-353"

    def fake_gh_json(*args: str) -> object:
        calls.append(args)
        endpoint = args[-1]
        if endpoint == f"repos/{repository}":
            return {
                "full_name": repository,
                "permissions": {"push": True},
                "allow_merge_commit": True,
            }
        if endpoint == f"repos/{repository}/branches/{encoded_branch}":
            return {"name": branch, "protected": True}
        if endpoint == f"repos/{repository}/rules/branches/{encoded_branch}":
            return [
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [{"context": "test"}],
                    },
                }
            ]
        if endpoint == f"repos/{repository}/branches/{encoded_branch}/protection":
            return {
                "required_status_checks": {
                    "strict": True,
                    "contexts": ["lint"],
                }
            }
        raise AssertionError(f"unexpected GitHub API endpoint: {endpoint}")

    monkeypatch.setattr(github_api, "_gh_json", fake_gh_json)

    policies = GitHubInspector().inspect(repository, (branch,))

    assert policies[0].branch == branch
    assert policies[0].required_checks == ("lint", "test")
    branch_endpoints = [args[-1] for args in calls if "/branches/" in args[-1]]
    assert branch_endpoints == [
        f"repos/{repository}/branches/{encoded_branch}",
        f"repos/{repository}/rules/branches/{encoded_branch}",
        f"repos/{repository}/branches/{encoded_branch}/protection",
    ]


def test_inspector_accepts_ruleset_protection_without_classic_protection(
    monkeypatch,
) -> None:
    repository = "owner/project"
    branch = "main"

    def fake_gh_json(*args: str) -> object:
        endpoint = args[-1]
        if endpoint == f"repos/{repository}":
            return {
                "full_name": repository,
                "permissions": {"push": True},
                "allow_squash_merge": True,
            }
        if endpoint == f"repos/{repository}/branches/{branch}":
            return {"name": branch, "protected": True}
        if endpoint == f"repos/{repository}/rules/branches/{branch}":
            return [
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [{"context": "test"}],
                    },
                }
            ]
        if endpoint == f"repos/{repository}/branches/{branch}/protection":
            raise GitHubInspectionError(
                "gh: Not Found (HTTP 404)",
                status_code=404,
            )
        raise AssertionError(f"unexpected GitHub API endpoint: {endpoint}")

    monkeypatch.setattr(github_api, "_gh_json", fake_gh_json)

    policies = GitHubInspector().inspect(repository, (branch,))

    assert policies[0].strict is True
    assert policies[0].required_checks == ("test",)


def test_inspector_surfaces_non_404_classic_protection_errors(
    monkeypatch,
) -> None:
    repository = "owner/project"
    branch = "main"

    def fake_gh_json(*args: str) -> object:
        endpoint = args[-1]
        if endpoint == f"repos/{repository}":
            return {
                "full_name": repository,
                "permissions": {"push": True},
                "allow_squash_merge": True,
            }
        if endpoint == f"repos/{repository}/branches/{branch}":
            return {"name": branch, "protected": True}
        if endpoint == f"repos/{repository}/rules/branches/{branch}":
            return []
        if endpoint == f"repos/{repository}/branches/{branch}/protection":
            raise GitHubInspectionError(
                "gh: Forbidden (HTTP 403)",
                status_code=403,
            )
        raise AssertionError(f"unexpected GitHub API endpoint: {endpoint}")

    monkeypatch.setattr(github_api, "_gh_json", fake_gh_json)

    with pytest.raises(GitHubInspectionError, match="Forbidden"):
        GitHubInspector().inspect(repository, (branch,))
