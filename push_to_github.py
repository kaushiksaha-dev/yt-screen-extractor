"""Helper script to push the local Git repository to GitHub using dulwich."""

import os
import sys
from dulwich import porcelain


def push_repo(repo_url: str, token: str = None, branch: str = "main"):
    repo_path = os.path.dirname(os.path.abspath(__file__))

    # Format URL with token for authenticated HTTPS push if provided
    if token and repo_url.startswith("https://"):
        # https://TOKEN@github.com/username/repo.git
        clean_url = repo_url.replace("https://", "")
        auth_url = f"https://{token}@{clean_url}"
    else:
        auth_url = repo_url

    print(f"Pushing to {repo_url} on branch '{branch}'...")
    try:
        porcelain.push(repo_path, auth_url, f"refs/heads/{branch}:refs/heads/{branch}")
        print("Successfully pushed to GitHub!")
    except Exception as e:
        print(f"Push failed: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python push_to_github.py <GITHUB_REPO_URL> [PERSONAL_ACCESS_TOKEN]")
        sys.exit(1)
    url = sys.argv[1]
    tok = sys.argv[2] if len(sys.argv) > 2 else None
    push_repo(url, tok)
