---
name: desktop-organizer
description: Audit ~/Downloads and ~/Desktop folders, group files by extension/age/size, and propose safe cleanup or organization.
category: productivity
version: 1.0.0
triggers: ["desktop-organizer", "don dep downloads", "dọn dẹp downloads", "sap xep file", "sắp xếp file", "organize downloads", "clean desktop"]
---

# Desktop Organizer Skill

Use this skill when the user wants to inspect, categorize, or organize cluttered files in `~/Downloads`, `~/Desktop`, or a target workspace folder.

## Workflow
1. Scan top-level files in `~/Downloads` (or the requested folder) and group them by category (Images, Videos, Archives, Documents, Code, Installers).
2. Report total file counts, largest files, and suggested destination subdirectories without deleting anything automatically.
3. Ask for confirmation before moving or deleting any user files.
