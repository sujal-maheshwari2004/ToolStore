import os
import json
import subprocess

TOOLS_DIR = "../tools"


# ------------------------------
# CLEAN FOLDER NAME
# ------------------------------
def safe_folder_name(name):
    return "".join(c for c in name if c.isalnum() or c in (' ', '_', '-')).rstrip()


# ------------------------------
# CLONE + REQUIREMENTS INSTALL
# ------------------------------
def clone_and_install(repo_url, folder_name, tools_dir=TOOLS_DIR):
    target_path = os.path.join(tools_dir, folder_name)

    print(f"\nCloning {repo_url} into {target_path}...")

    if os.path.exists(target_path):
        print(f"Folder {target_path} already exists, skipping clone.")
    else:
        result = subprocess.run(
            ["git", "clone", repo_url, target_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        print(result.stdout.decode(), result.stderr.decode())

    # Install requirements if present
    req_file = os.path.join(target_path, "requirements.txt")

    if os.path.isfile(req_file):
        print(f"Installing requirements from {req_file}...")
        res = subprocess.run(
            ["pip", "install", "-r", req_file],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        print(res.stdout.decode(), res.stderr.decode())
    else:
        print("No requirements.txt found, skipping pip install.")


# ------------------------------
# MAIN BULK PROCESSOR (REUSABLE)
# ------------------------------
def process_tools(json_file, tools_dir=TOOLS_DIR):
    if not os.path.exists(tools_dir):
        os.makedirs(tools_dir)

    with open(json_file, "r", encoding="utf-8") as f:
        entries = json.load(f)

    for entry in entries:
        repo_url = entry.get("tool_git_link")
        tool_name = safe_folder_name(entry.get("tool_name", "repo"))

        if repo_url and repo_url.startswith("https://"):
            clone_and_install(repo_url, tool_name, tools_dir)
        else:
            print(f"Skipping: invalid repo URL for {tool_name}")
