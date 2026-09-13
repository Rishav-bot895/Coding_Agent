"""localdev: Native Windows Single-File Offline Python Coding Agent.

A personal portfolio project demonstrating high engineering rigor, clean Windows
systems integration, deterministic static analysis, and reproducible local AI agent
orchestration.

CRITICAL SAFETY AND TRUST BOUNDARIES:
- Platform: Windows 11 x64 only.
- Scope: Exactly one explicit Python source target per command.
- Trust: For user-owned or trusted Python code only.
- Operational Limits: Job Objects, execution timeouts, and output byte caps are
  operational containment guardrails to terminate runaways; they DO NOT constitute
  a security sandbox.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]

