from github import Github, Auth
import os
import json

auth = Auth.Token(os.environ.get("GITHUB_TOKEN"))
g = Github(auth=auth)
repo = g.get_repo(os.environ.get("REPO_NAME"))

FILE_PATH = os.environ.get("FILE_PATH")
BRANCH = os.environ.get("BRANCH")


def load_db():
    """
    Always return latest file + sha from GitHub.
    """
    try:
        file = repo.get_contents(FILE_PATH, ref=BRANCH)
        data = json.loads(file.decoded_content.decode())
        return data, file
    except Exception:
        return {"roles": {}, "blacklist": []}, None


def save_db(data, file=None):
    """
    Save to GitHub.
    If file is stale, refetch before updating.
    """
    new_content = json.dumps(data, indent=2)

    if file:
        # Always refresh sha before update
        latest = repo.get_contents(FILE_PATH, ref=BRANCH)
        repo.update_file(
            path=latest.path,
            message="update data.json",
            content=new_content,
            sha=latest.sha,
            branch=BRANCH,
        )
    else:
        repo.create_file(
            path=FILE_PATH,
            message="create data.json",
            content=new_content,
            branch=BRANCH,
        )
