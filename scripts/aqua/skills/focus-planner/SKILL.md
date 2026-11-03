---
name: focus-planner
description: Organize Pomodoro focus sessions, review upcoming Todo and Weekly Planner events, and balance daily mood/energy.
triggers: ["focus-planner", "pomodoro", "ke hoach hoc tap", "lich trinh", "focus session", "tap trung"]
---

# Focus Planner Skill

Use this skill when the user wants to plan a focus block, review today's tasks/events, or adjust workload to match their current mood and energy.

## Workflow
1. Check the user's current Mood & Energy check-in state and upcoming Weekly Planner / Todo items.
2. Recommend a realistic Pomodoro cadence (e.g. 25/5 for steady energy or 15/5 when drained/low).
3. Offer to create an automated Companion rule (`focus_end` or `schedule_time`) if the user wants a follow-up check.
