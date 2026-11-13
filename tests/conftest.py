"""Shared fixtures.

pytest-homeassistant enables custom integrations by default, and the minimum
Home Assistant is 2026.11, so no auto_enable_custom_integrations fixture is
needed. Integration fixtures (a mocked client, the config entry) arrive with
the config flow.
"""
