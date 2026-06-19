# Talks to the GitHub API as the App: auth, fetching the diff, posting reviews.
#
# Biggest gotcha I ran into building this: GitHub's review endpoint rejects
# any inline comment if the line number doesn't actually exist in the diff
# (you get a 422 with not much explanation). The model can't be trusted to
# only point at real lines, so below I parse the diff myself first to get
# the actual set of valid lines, and filter the model's comments against
# that before sending anything to GitHub.
import logging
import time
from typing import Iterable

import jwt  # PyJWT
import requests

import config

logger = logging.getLogger("pr_reviewer.github")

GITHUB_API = "https://api.github.com"
_TIMEOUT = 30


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
def _app_jwt() -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,            # backdate 60s to tolerate clock skew
        "exp": now + (9 * 60),      # max allowed is 10 minutes; use 9 for safety
        "iss": config.GITHUB_APP_ID,
    }
    token = jwt.encode(payload, config.GITHUB_APP_PRIVATE_KEY, algorithm="RS256")
    # PyJWT >= 2 returns str already; guard for bytes from older versions.
    return token.decode("utf-8") if isinstance(token, bytes) else token


def installation_token(installation_id: str) -> str:
    """Exchange the App JWT for an installation access token."""
    resp = requests.post(
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {_app_jwt()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def _auth_headers(token: str, accept: str = "application/vnd.github+json") -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "github-pr-reviewer",
    }


# --------------------------------------------------------------------------- #
# Fetch diff
# --------------------------------------------------------------------------- #
def fetch_pr_diff(token: str, repo_full_name: str, pr_number: int) -> str:
    """Return the unified diff text for a PR."""
    resp = requests.get(
        f"{GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}",
        headers=_auth_headers(token, accept="application/vnd.github.v3.diff"),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


# --------------------------------------------------------------------------- #
# Diff parsing -> commentable lines
# --------------------------------------------------------------------------- #
def parse_commentable_lines(diff_text: str) -> dict[str, set[int]]:
    """For each file, return the set of new-file line numbers we're allowed
    to comment on (i.e. lines that were actually added in the diff).

    Quick refresher on unified diff format since I kept losing track of this:
        +++ b/<path>
        @@ -<old_start>,<old_len> +<new_start>,<new_len> @@
        ' ' context line  -> counts toward new line number
        '+' added line    -> counts toward new line number, commentable
        '-' removed line  -> doesn't exist in the new file, skip it
    """
    commentable: dict[str, set[int]] = {}
    current_path: str | None = None
    new_line_no = 0

    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            if target == "/dev/null":
                current_path = None
            else:
                # Strip the leading "b/" GitHub adds.
                current_path = target[2:] if target.startswith("b/") else target
                commentable.setdefault(current_path, set())
            continue

        if raw.startswith("--- "):
            # old-file marker; ignore (we track the new file via +++).
            continue

        if raw.startswith("@@"):
            # Hunk header: @@ -a,b +c,d @@
            try:
                plus = raw.split("+", 1)[1]
                new_start = plus.split(",")[0].split(" ")[0]
                new_line_no = int(new_start)
            except (IndexError, ValueError):
                new_line_no = 0
            continue

        if current_path is None or new_line_no == 0:
            continue

        if raw.startswith("+"):
            commentable[current_path].add(new_line_no)
            new_line_no += 1
        elif raw.startswith("-"):
            # removed line: does not exist in the new file
            pass
        elif raw.startswith("\\"):
            # "\ No newline at end of file"
            pass
        else:
            # context line (starts with a space, or blank)
            new_line_no += 1

    # Drop files that ended up with no commentable lines.
    return {p: lines for p, lines in commentable.items() if lines}


# --------------------------------------------------------------------------- #
# Post review
# --------------------------------------------------------------------------- #
def post_review(
    token: str,
    repo_full_name: str,
    pr_number: int,
    commit_sha: str,
    summary: str,
    comments: list[dict],
) -> int:
    # comments: list of {"path", "line", "side": "RIGHT", "body"}
    # If GitHub rejects the inline batch, retries as a summary-only comment
    # instead of just losing the review entirely.
    review_body = {
        "commit_id": commit_sha,
        "body": summary,
        "event": "COMMENT",
        "comments": comments,
    }

    resp = requests.post(
        f"{GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}/reviews",
        headers=_auth_headers(token),
        json=review_body,
        timeout=_TIMEOUT,
    )

    if resp.status_code in (200, 201):
        return len(comments)

    # Inline batch rejected (commonly 422 for a line GitHub won't accept).
    logger.warning(
        "Inline review rejected (status=%s): %s. Falling back to summary-only.",
        resp.status_code, resp.text[:500],
    )

    fallback = {
        "commit_id": commit_sha,
        "body": _summary_with_inlined_comments(summary, comments),
        "event": "COMMENT",
    }
    fb_resp = requests.post(
        f"{GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}/reviews",
        headers=_auth_headers(token),
        json=fallback,
        timeout=_TIMEOUT,
    )
    fb_resp.raise_for_status()
    return 0


def _summary_with_inlined_comments(summary: str, comments: Iterable[dict]) -> str:
    """Render comments into the review body when inline posting failed."""
    lines = [summary, "", "---", "", "**Inline findings (could not attach to lines):**", ""]
    for c in comments:
        lines.append(f"- `{c['path']}:{c['line']}` — {c['body']}")
    return "\n".join(lines)
