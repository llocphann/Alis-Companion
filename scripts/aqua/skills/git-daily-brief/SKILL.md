---
name: git-daily-brief
description: Summarize Git branch status, uncommitted diffs, and recent commits across active project repositories.
category: development
version: 1.0.0
triggers: ["git-daily-brief", "tom tat git", "tóm tắt git", "bao cao git", "báo cáo git", "git summary", "daily git brief"]
---

# Git Daily Brief Skill

Use this skill when the user asks for a quick daily briefing of their Git repository or active project workspaces (branch, uncommitted changes, and recent commit log).

## Workflow
1. Call `git-mcp.git_status`, `git-mcp.git_branch`, and `git-mcp.git_log` on the active project workspace.
2. Summarize the current branch, modified/untracked files, and the latest 5 commits.
3. Highlight any uncommitted work or pending merges that need attention.
