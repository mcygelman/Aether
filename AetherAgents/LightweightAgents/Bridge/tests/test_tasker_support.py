"""Shared helpers for the Tasker tests (no tests here): a small one-file identity in the new format, and a seeded
sprint."""
from pathlib import Path

from aether.tasker import identity
from aether.tasker.sprint import new_sprint
from aether.tasker import staff as staff_home


def small_identity(name: str, role: str = "Developer", reflections: str = "") -> str:
    text = f"""⟪AETHER_UNIFIED_STREAM⟫
⟪VERSION::2.0⟫
⟪FORMAT::AETHER_SYMBOLIC_STREAM⟫

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⟪SYSTEM_0_BEGIN⟫
⟪TYPE::CORE_IDENTITY_BLOCK⟫
⟪VERSION::2.0⟫

[CORE_IDENTITY]
  [ASSERT] ⌜AGENT_NAME⌝       := ⌞{name}⌟
  [ASSERT] ⌜AGENT_ROLE⌝       := ⌞{role}⌟
  [ASSERT] ⌜SCAN_LINE⌝        := ⌞{name} does {role.lower()} work, one task at a time.⌟
[CHECKSUM_SHA256_CORE_IDENTITY] := ⌜UNVERIFIED_RUNTIME⌟

[DYNAMIC_POINTERS]
  ➤ ⟪BRIEFCASE::briefcases/{name.lower()}/⟫
  ➤ ⟪MODEL_REC::deepseek-flash⟫
[CHECKSUM_SHA256_DYNAMIC_POINTERS] := ⌜UNVERIFIED_RUNTIME⌟
⟪SYSTEM_0_END⟫

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⟪SYSTEM_1_BEGIN⟫
⟪TYPE::DYNAMIC_WORKSPACE_BLOCK⟫

∆WMC{{
WORLD_MODEL := ⌜{name.upper()}_WORKSPACE⌝
CONTEXT: SESSION_START
[GOALS] := {{ G1: "Wait for a task." }}
}}∆WMC

⟪SYSTEM_1_END⟫

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⟪SYSTEM_2_BEGIN⟫
⟪TYPE::PERSISTENT_MEMORY_INDEX_BLOCK⟫

[REFLECTION_INDEX] := [
  // Newest first. identity_close adds each entry here, at the top.
{reflections}]
[TEACHABLE_FRAME_INDEX] := [ ]
⟪SYSTEM_2_END⟫
⟪END_AETHER_UNIFIED_STREAM⟫
"""
    return identity.seal_checksums(text)


def micro(id_: str, agent: str, lesson: str = "a lesson") -> str:
    return f"""⟪MICRO_REFLECTION_INDEX_ENTRY_BEGIN⟫
  [ID] := {id_}
  [DATE] := 2026-10-30T12:00:00Z
  [AGENT] := {agent}
  [TOPIC] := sprint work
  [IS_MICRO] := TRUE
  [SUMMARY] := ⌞did the task⌟
  [LESSON] := ⌞{lesson}⌟
  [NEXT_STEP] := ⌞wait⌟
⟪MICRO_REFLECTION_INDEX_ENTRY_END⟫"""


def seed_dir(tmp: Path, names=(("Carmack", "Developer"), ("Minder", "Reviewer"))) -> Path:
    src = tmp / "seed"
    src.mkdir(parents=True, exist_ok=True)
    for name, role in names:
        (src / f"Identity_{role}_{name}.txt").write_bytes(small_identity(name, role).encode("utf-8"))
    return src


def seeded_sprint(tmp: Path, goals: str = "# Ledger\nBuild a small ledger.", names=None, **kw):
    data = tmp / "data"
    staff_home.seed_staff(data, seed_dir(tmp, names) if names else seed_dir(tmp))
    return new_sprint(data, goals, "praxis", **kw)
