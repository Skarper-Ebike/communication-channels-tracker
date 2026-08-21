import os

from app.setup.wizard import update_env


def test_update_env_creates_file_with_owner_only_permissions(paths):
    update_env(paths, {"JIRA_EMAIL": "a@b.com", "JIRA_API_TOKEN": "tok"})
    content = paths.env_file.read_text()
    assert "JIRA_EMAIL=a@b.com" in content
    assert "JIRA_API_TOKEN=tok" in content
    assert oct(os.stat(paths.env_file).st_mode & 0o777) == "0o600"


def test_update_env_preserves_unrelated_lines_and_comments(paths):
    paths.ensure()
    paths.env_file.write_text("# my notes\nOTHER=keep\nJIRA_EMAIL=old@b.com\n")
    update_env(paths, {"JIRA_EMAIL": "new@b.com"})
    lines = paths.env_file.read_text().splitlines()
    assert "# my notes" in lines
    assert "OTHER=keep" in lines
    assert "JIRA_EMAIL=new@b.com" in lines
    assert "JIRA_EMAIL=old@b.com" not in lines
