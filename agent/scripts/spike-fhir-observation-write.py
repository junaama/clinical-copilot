#!/usr/bin/env python
# ruff: noqa: N999
"""Executable wrapper for the FHIR Observation POST spike."""

from __future__ import annotations

from spike_fhir_observation_write import main

if __name__ == "__main__":
    raise SystemExit(main())
