"""memory_fabric: the Aether Memory Fabric engine.

One agent's mind lives in one SQLite "cartridge" file (data only). This
package is the shared engine (part of the ecosystem, not of any agent) that
holds all the logic, organised as replaceable boxes with fixed inputs and
outputs:

    clock        time, injectable for tests and simulation
    schema       what a cartridge contains; shared vocabularies
    cartridge    create/open/close, seal, settings, profiles
    decay        two-phase decay maths (Python + temporary SQL views)
    kinds        agents choose kinds in words; mapped to profiles
    fabric       writing memory: remember, link, bury, raise, adjust
    recall       tags in, ranked short headers out; load what is chosen
    reformation  agent-led context reformation when the board fills
    triggers     which moments call for a memory check
    staff        reviews, verified outcomes, levels, trading card, résumé
    sidelog      a journal of actions beside the memories: append-only, sealed, not captured
    export       clean copy for sharing (proprietary removed)
    host         the one call a runtime makes: event + tags -> board text
    tools        the agent's own hands: model-callable memory tools
    belt         adds memory tools to an existing Bridge tool belt (wrapper)
    hydration    session start: directive + orientation + task recall, budgeted
    viewer       read-only picture of a mind: snapshot JSON + one HTML page
    importer     migrate an agent's markdown memory folder into a cartridge
    movein       the agent's walkthrough and its own move-in sheet
    archive      frozen, read-only reference archive of a mind in an older structure
    migrations   carry a cartridge forward to a new schema (backup first)
    tokens       one replaceable token estimator (host tokenizer pluggable)
    errors       one error family; every refusal says how to fix it
    simulator    scenario library runner (the calibration bench)
    engine       MemoryEngine: the single front door

Standard library only. Python 3.10+.
"""
from .clock import FixedClock, SystemClock
from .engine import MemoryEngine
from .errors import MemoryFabricError
from .reformation import BoardItem, Decision, SetDown
from .tools import MemoryTools, ToolLimits
from .triggers import Event

__version__ = "0.2.0"

__all__ = ["MemoryEngine", "MemoryFabricError", "MemoryTools", "ToolLimits", "FixedClock",
           "SystemClock", "BoardItem", "Decision", "SetDown", "Event", "__version__"]
