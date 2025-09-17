from github import Github, Auth
import os
import json

auth = Auth.Token(os.environ.get("GITHUB_TOKEN"))
g = Github(auth=auth)
repo = g.get_repo(os.environ.get("REPO_NAME"))

def load_db():
    try:
        file = repo.get_contents(os.environ.get("FILE_PATH"), ref=os.environ.get("BRANCH"))
        data = json.loads(file.decoded_content.decode())
        return data, file
    except Exception:
        return {"roles": {}, "blacklist": []}, None

def save_db(data, file=None):
    new_content = json.dumps(data, indent=2)
    if file:
        repo.update_file(
            path=file.path,
            message="update data.json",
            content=new_content,
            sha=file.sha,
            branch=os.environ.get("BRANCH")
        )
    else:
        repo.create_file(
            path=os.environ.get("FILE_PATH"),
            message="create data.json",
            content=new_content,
            branch=os.environ.get("BRANCH")
        )