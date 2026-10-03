"""Authenticated principals. Tenant and role come from verified claims."""

from opsmesh_api.identity.principal import AgentPrincipal, Principal

__all__ = ["AgentPrincipal", "Principal"]
