"""
Signal aggregation for the PR Flywheel.

This module normalizes and consolidates signals from multiple sources:
- Review swarm findings (from parallel reviewers)
- CI check runs (via PyGithub API)
- Human and AI reviews (PR review comments)
- Code scanning alerts (via GitHub CLI)
- Unresolved review threads (via GraphQL)
- De-duplicates findings by issue_id
- Produces a unified signals.json for the decision engine
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from typing import Any

from github import Github

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _run_gh_cli(args: list[str], error_msg: str) -> str:
    """
    Execute a GitHub CLI command and return the output.

    Args:
        args: Command arguments (e.g., ["api", "/repos/..."])
        error_msg: Error message prefix for logging

    Returns:
        Command stdout as string, or empty JSON structure on failure
    """
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,  # 30 second timeout
        )
        if result.returncode == 0:
            return result.stdout
        else:
            logger.warning(f"{error_msg}: gh CLI returned code {result.returncode}")
            logger.debug(f"stderr: {result.stderr}")
            return ""
    except subprocess.TimeoutExpired:
        logger.warning(f"{error_msg}: gh CLI command timed out after 30 seconds")
        return ""
    except FileNotFoundError:
        logger.warning(f"{error_msg}: gh CLI not found in PATH")
        return ""
    except Exception as e:
        logger.warning(f"{error_msg}: {e}")
        return ""


def _get_required_env_var(name: str) -> str:
    """
    Get a required environment variable or raise an error.

    Args:
        name: Environment variable name

    Returns:
        Environment variable value

    Raises:
        ValueError: If the environment variable is not set or is empty
    """
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def load_swarm_findings(path: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Load swarm findings from JSON file.

    Args:
        path: Path to swarm findings JSON file

    Returns:
        Tuple of (findings list, reviewer_coverage dict)
    """
    with open(path, "r") as f:
        data = json.load(f)
    return data["findings"], data["reviewer_coverage"]


def normalize_check_runs(check_runs: list[Any]) -> list[dict[str, Any]]:
    """
    Normalize PyGithub check run objects to dicts.

    Args:
        check_runs: List of PyGithub CheckRun objects

    Returns:
        List of dicts with keys: name, conclusion, details_url, output_summary
    """
    normalized = []
    for check in check_runs:
        output_summary = ""
        if check.output is not None and check.output.summary is not None:
            output_summary = check.output.summary[:2000]  # Truncate to 2000 chars

        normalized.append(
            {
                "name": check.name,
                "conclusion": check.conclusion,
                "details_url": check.details_url,
                "output_summary": output_summary,
            }
        )

    return normalized


def classify_review_comment(comment: Any) -> str:
    """
    Classify a review comment as AI or human.

    Args:
        comment: PyGithub comment object

    Returns:
        "ai_reviews" for Bot-type users or users with "copilot" in login,
        "human_reviews" otherwise
    """
    if comment.user.type == "Bot":
        return "ai_reviews"

    if "copilot" in comment.user.login.lower():
        return "ai_reviews"

    return "human_reviews"


def normalize_human_review(review: Any) -> dict[str, Any]:
    """
    Normalize a PR review object to dict.

    Args:
        review: PyGithub PullRequestReview object

    Returns:
        Dict with keys: id, state, author, body
    """
    return {
        "id": review.id,
        "state": review.state,
        "author": review.user.login,
        "body": review.body or "",
    }


def parse_code_scanning_alerts(raw_json: str) -> list[dict[str, Any]]:
    """
    Parse code scanning alerts from JSON string.

    Args:
        raw_json: JSON string from GitHub code scanning API

    Returns:
        List of dicts with keys: rule, severity, path
    """
    if not raw_json:
        return []

    try:
        alerts = json.loads(raw_json)
        result = []

        for alert in alerts:
            # Extract rule info - handle both nested structure
            rule_obj = alert.get("rule", {})

            # The rule may have both id and severity fields
            rule_id = rule_obj.get("id", "")
            severity = rule_obj.get("severity", "")

            # Extract path from most_recent_instance
            path = (
                alert.get("most_recent_instance", {})
                .get("location", {})
                .get("path", "")
            )

            result.append({"rule": rule_id, "severity": severity, "path": path})

        return result
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(
            f"Failed to parse code scanning alerts: {e}, returning empty list"
        )
        return []


def parse_unresolved_threads(graphql_json: str) -> list[dict[str, Any]]:
    """
    Parse unresolved review threads from GraphQL JSON response.

    Args:
        graphql_json: JSON string from GitHub GraphQL API

    Returns:
        List of dicts with keys: path, line, body (truncated to 500 chars), author
    """
    if not graphql_json:
        return []

    try:
        data = json.loads(graphql_json)
        threads = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes", [])
        )

        result = []
        for thread in threads:
            if thread.get("isResolved", True):
                continue  # Skip resolved threads

            comments = thread.get("comments", {}).get("nodes", [])
            if not comments:
                continue

            # Extract first comment
            first_comment = comments[0]
            body = first_comment.get("body", "")[:500]  # Truncate to 500 chars

            result.append(
                {
                    "path": first_comment.get("path", ""),
                    "line": first_comment.get("position"),
                    "body": body,
                    "author": first_comment.get("author", {}).get("login", ""),
                }
            )

        return result
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("Failed to parse unresolved threads, returning empty list")
        return []


def build_signals(
    pr_number: int,
    head_sha: str,
    swarm_path: str,
    checks: list[Any],
    reviews: list[Any],
    comments: list[Any],
    alerts_json: str,
    threads_json: str,
) -> dict[str, Any]:
    """
    Build unified signals dict from all sources.

    Args:
        pr_number: PR number
        head_sha: Commit SHA
        swarm_path: Path to swarm findings JSON
        checks: List of PyGithub CheckRun objects
        reviews: List of PyGithub PullRequestReview objects
        comments: List of PyGithub review comment objects
        alerts_json: JSON string of code scanning alerts
        threads_json: GraphQL JSON string of review threads

    Returns:
        Dict with keys: pr_number, head_sha, swarm_findings, reviewer_coverage,
        checks, ai_reviews, human_reviews, unresolved_threads, security
    """
    # Load swarm findings
    swarm_findings, reviewer_coverage = load_swarm_findings(swarm_path)

    # De-duplicate findings by issue_id
    seen_ids = set()
    deduplicated_findings = []
    for finding in swarm_findings:
        issue_id = finding.get("issue_id")
        if issue_id not in seen_ids:
            seen_ids.add(issue_id)
            deduplicated_findings.append(finding)

    # Normalize check runs
    normalized_checks = normalize_check_runs(checks)

    # Classify and normalize reviews
    human_reviews = []
    ai_reviews = []

    for review in reviews:
        classification = classify_review_comment(review)
        normalized = normalize_human_review(review)

        if classification == "ai_reviews":
            ai_reviews.append(normalized)
        else:
            human_reviews.append(normalized)

    # Classify comments (separate from reviews)
    for comment in comments:
        classification = classify_review_comment(comment)
        normalized = normalize_human_review(comment)

        if classification == "ai_reviews":
            ai_reviews.append(normalized)
        else:
            human_reviews.append(normalized)

    # Parse security alerts
    security_alerts = parse_code_scanning_alerts(alerts_json)

    # Parse unresolved threads
    unresolved_threads = parse_unresolved_threads(threads_json)

    return {
        "pr_number": pr_number,
        "head_sha": head_sha,
        "swarm_findings": deduplicated_findings,
        "reviewer_coverage": reviewer_coverage,
        "checks": normalized_checks,
        "human_reviews": human_reviews,
        "ai_reviews": ai_reviews,
        "unresolved_threads": unresolved_threads,
        "security": security_alerts,
    }


def main() -> None:
    """
    Main entry point for signal aggregator CLI.

    Reads swarm findings, fetches PR data from GitHub, aggregates all signals,
    and outputs unified JSON to stdout.
    """
    parser = argparse.ArgumentParser(description="Aggregate PR review signals")
    parser.add_argument(
        "--swarm-findings", required=True, help="Path to swarm findings JSON"
    )
    args = parser.parse_args()

    # Read environment variables with validation
    try:
        gh_token = _get_required_env_var("GH_TOKEN")
        repo_name = _get_required_env_var("REPO")
        pr_number_str = _get_required_env_var("PR_NUMBER")
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    try:
        pr_number = int(pr_number_str)
    except ValueError:
        logger.error(f"Invalid PR_NUMBER: {pr_number_str} (must be an integer)")
        sys.exit(1)

    # Initialize PyGithub
    g = Github(gh_token)
    repo = g.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    # Get HEAD SHA
    head_sha = pr.head.sha

    # Fetch check runs for HEAD SHA
    check_runs_data = repo.get_commit(head_sha).get_check_runs()
    checks = list(check_runs_data)

    # Fetch reviews
    reviews = list(pr.get_reviews())

    # Fetch review comments
    comments = list(pr.get_review_comments())

    # Fetch code scanning alerts via gh CLI
    alerts_json = _run_gh_cli(
        ["api", f"/repos/{repo_name}/code-scanning/alerts", "--jq", "."],
        "Failed to fetch code scanning alerts",
    )
    if not alerts_json:
        alerts_json = "[]"

    # Fetch unresolved threads via gh CLI GraphQL
    graphql_query = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          reviewThreads(first: 100) {
            nodes {
              isResolved
              comments(first: 1) {
                nodes {
                  path
                  position
                  body
                  author {
                    login
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    try:
        owner, name = repo_name.split("/")
    except ValueError:
        logger.error(f"Invalid REPO format: {repo_name} (expected 'owner/name')")
        sys.exit(1)

    graphql_input = json.dumps(
        {
            "query": graphql_query,
            "variables": {"owner": owner, "name": name, "number": pr_number},
        }
    )

    threads_json = _run_gh_cli(
        ["api", "graphql", "-f", f"query={graphql_input}"],
        "Failed to fetch unresolved threads",
    )
    if not threads_json:
        threads_json = "{}"

    # Build unified signals
    signals = build_signals(
        pr_number=pr_number,
        head_sha=head_sha,
        swarm_path=args.swarm_findings,
        checks=checks,
        reviews=reviews,
        comments=comments,
        alerts_json=alerts_json,
        threads_json=threads_json,
    )

    # Output JSON to stdout
    print(json.dumps(signals, indent=2))


if __name__ == "__main__":
    main()
