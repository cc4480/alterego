"""Capability seams for pc-mcp-bridge.

Every capability has three roles:
- Service Definition (seams.definitions): the machine-readable contract.
- Service Provider (seams.providers.<kind>): the implementation.
- Consumer (tool_wrappers -> toolcall.call): references definitions only;
  toolcall resolves the provider function via seams.registry.

One role alone is not a seam.
"""
