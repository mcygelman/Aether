"""The Aether Tasker Process (Michel's design, 2026-10-30): the Tasker, the sprint folder, the Staff Home,
briefcases, the staff turn, the Aether Timer and the headless CLI.

Whose turn it is lives in data (the Tasker table), never in prose. The Timer only carries.
Contract: AetherPlanAndAnalysis/Specifications/Revamp/Delegation/00_TASKER_PROCESS_CONTRACT.md
"""
from .tasker import Change, Task, Tasker, TaskerError, STATUSES

__all__ = ["Tasker", "Task", "Change", "TaskerError", "STATUSES"]
