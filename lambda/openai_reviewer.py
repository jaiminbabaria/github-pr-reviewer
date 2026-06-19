# Sends the diff to gpt-4o and asks for structured JSON back.
#
# I don't fully trust the model to only reference real line numbers, so I
# pass it the list of valid lines per file and then double-check every
# comment it returns against that list afterward. Anything that doesn't
# match gets dropped (and mentioned in the summary so it's not just lost).
import json
import logging

from openai import OpenAI

import config

logger = logging.getLogger("pr_reviewer.openai")

_client = OpenAI(api_key=config.OPENAI_API_KEY)

VALID_CATEGORIES = {"bug", "security", "performance", "style", "error-handling"}
VALID_SEVERITIES = {"low", "medium", "high"}

SYSTEM_PROMPT = """You are a senior software engineer performing a rigorous pull request code review.
Review ONLY the changes shown in the unified diff. Focus on:
  1. Potential bugs and logic errors
  2. Security vulnerabilities (injection, secrets, auth, unsafe deserialization, etc.)
  3. Performance issues
  4. Code style and best practices
  5. Missing error handling

Rules:
- Comment only on lines that are in the provided "commentable_lines" set. Never invent line numbers.
- Each comment must be specific and actionable. No vague praise.
- If a file has no real issues, do not comment on it.
- Prefer fewer, high-signal comments over many low-value ones.
- "side" is always "RIGHT" (the new version of the file).

Respond with STRICT JSON only (no markdown, no prose) in exactly this schema:
{
  "summary": "2-5 sentence overview of the PR quality and the main themes of your findings",
  "comments": [
    {
      "path": "relative/file/path.py",
      "line": 42,
      "side": "RIGHT",
      "category": "bug|security|performance|style|error-handling",
      "severity": "low|medium|high",
      "body": "Clear explanation of the issue and a concrete suggested fix."
    }
  ]
}
"""


def review_diff(diff_text: str, commentable: dict[str, set[int]]) -> dict:
    """Return {"summary": str, "comments": [validated comment dicts]}."""
    if not commentable:
        return {
            "summary": "No added lines were found in this diff to review.",
            "comments": [],
        }

    truncated = diff_text[: config.MAX_DIFF_CHARS]
    was_truncated = len(diff_text) > config.MAX_DIFF_CHARS

    # JSON sets are not serializable; convert to sorted lists for the prompt.
    commentable_for_prompt = {p: sorted(lines) for p, lines in commentable.items()}

    user_content = (
        f"commentable_lines (path -> allowed line numbers):\n"
        f"{json.dumps(commentable_for_prompt)}\n\n"
        f"{'NOTE: diff was truncated for length; review what is shown.' if was_truncated else ''}\n\n"
        f"UNIFIED DIFF:\n{truncated}"
    )

    completion = _client.chat.completions.create(
        model=config.OPENAI_MODEL,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )

    raw = completion.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("OpenAI returned non-JSON content: %s", raw[:500])
        return {"summary": "Automated review failed to parse the model response.", "comments": []}

    summary = str(parsed.get("summary", "")).strip() or "Automated review completed."
    raw_comments = parsed.get("comments", []) or []

    valid_comments, dropped = _validate_comments(raw_comments, commentable)

    if dropped:
        summary += (
            f"\n\n_({len(dropped)} additional finding(s) referenced lines outside the diff "
            f"and were omitted from inline comments.)_"
        )

    # Cap the number of inline comments to avoid spamming a PR.
    if len(valid_comments) > config.MAX_COMMENTS:
        extra = len(valid_comments) - config.MAX_COMMENTS
        valid_comments = valid_comments[: config.MAX_COMMENTS]
        summary += f"\n\n_(Showing top {config.MAX_COMMENTS} findings; {extra} more were truncated.)_"

    return {"summary": summary, "comments": valid_comments}


def _validate_comments(raw_comments: list, commentable: dict[str, set[int]]):
    valid: list[dict] = []
    dropped: list[dict] = []

    for c in raw_comments:
        if not isinstance(c, dict):
            continue
        path = c.get("path")
        line = c.get("line")
        body = (c.get("body") or "").strip()

        if not path or not isinstance(line, int) or not body:
            dropped.append(c)
            continue
        if path not in commentable or line not in commentable[path]:
            dropped.append(c)
            continue

        category = c.get("category", "style")
        if category not in VALID_CATEGORIES:
            category = "style"
        severity = c.get("severity", "low")
        if severity not in VALID_SEVERITIES:
            severity = "low"

        # Prefix the body with category/severity for quick scanning in the PR.
        decorated = f"**[{category} · {severity}]** {body}"

        valid.append(
            {
                "path": path,
                "line": line,
                "side": "RIGHT",
                "body": decorated,
            }
        )

    return valid, dropped
