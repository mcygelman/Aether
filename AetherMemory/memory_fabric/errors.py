"""Errors box: one family for every refusal the engine makes.

Inputs:  none.
Outputs: MemoryFabricError, the base class of every engine error
         (CartridgeError, FabricError, UnknownKind, ReformationError,
         StaffError, ExportError, ScenarioError, MemoryToolError,
         MigrationError, ImportError_).

A caller (the Bridge's tool dispatcher, the CLI, a test) can catch this one
class and turn any engine refusal into a message the agent sees and can
recover from, never a crash. Every message says what was wrong and what the
valid choices are.
"""


class MemoryFabricError(Exception):
    """Base class: the engine refused an operation; the message says why."""
