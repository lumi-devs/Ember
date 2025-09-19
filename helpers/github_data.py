from github import Github, Auth
import os
import json

auth = Auth.Token(os.environ.get("GITHUB_TOKEN"))
g = Github(auth=auth)
repo = g.get_repo(os.environ.get("REPO_NAME"))

BRANCH = os.environ.get("BRANCH")


def load_db(file_path: str = None):
    """
    Always return latest file + sha from GitHub.
    """
    try:
        file = repo.get_contents(file_path, ref=BRANCH)
        data = json.loads(file.decoded_content.decode())
        return data, file
    except Exception:
        return {"roles": {}, "blacklist": []}, None


def save_db(data, file=None, file_path: str = None):
    new_content = json.dumps(data, indent=2)

    try:
        latest = repo.get_contents(file_path, ref=BRANCH)
        # File exists → update
        repo.update_file(
            path=latest.path,
            message=f"update {file_path}",
            content=new_content,
            sha=latest.sha,
            branch=BRANCH,
        )
        print("updated "+ file_path)
    except Exception:
        # File does not exist → create
        repo.create_file(
            path=file_path,
            message=f"create {file_path}",
            content=new_content,
            branch=BRANCH,
        )
        print("created "+file_path)
